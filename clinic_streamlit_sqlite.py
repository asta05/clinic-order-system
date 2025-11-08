# clinic_streamlit_sqlite.py

import sqlite3
from datetime import datetime
import streamlit as st
import pandas as pd
import io
import os

# Merchant & static QR path (change if needed)
MERCHANT_VPA = "snekhaganesh87@okhdfcbank"
MERCHANT_NAME = "Snekha Ganesh"
STATIC_QR_PATH = r"C:\Users\G Astalakshmi\Desktop\project lite and lit\qr_snekha.png.jpg"

DB_PATH = "clinic_sqlite.db"

# ---------------- DB helpers ----------------
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def initialize_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT NOT NULL UNIQUE
    );
    CREATE TABLE IF NOT EXISTS tablets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        stock INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        order_date TEXT NOT NULL,
        FOREIGN KEY(customer_id) REFERENCES customers(id)
    );
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        tablet_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        FOREIGN KEY(order_id) REFERENCES orders(id),
        FOREIGN KEY(tablet_id) REFERENCES tablets(id)
    );
    """)
    conn.commit()
    conn.close()

# Seed list (single source of truth)
SEED_TABLETS = [
    ("Paracetamol 500mg", 20.0, 100),
    ("Ibuprofen 200mg", 25.0, 80),
    ("Cetirizine 10mg", 15.0, 120),
    ("Amoxicillin 500mg", 60.0, 50),
    ("Multivitamin", 40.0, 60),
    ("Aspirin 75mg", 18.0, 70),
    ("Omeprazole 20mg", 30.0, 40),
    ("Azithromycin 250mg", 55.0, 30),
    ("Loratadine 10mg", 22.0, 90),
    ("Calcium + Vitamin D", 45.0, 50)
]

def sync_seed_tablets():
    """Insert any seed tablets that are missing (preserve existing DB)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM tablets")
    existing = set(r[0] for r in cur.fetchall())
    to_insert = [s for s in SEED_TABLETS if s[0] not in existing]
    if to_insert:
        cur.executemany("INSERT INTO tablets (name, price, stock) VALUES (?, ?, ?)", to_insert)
        conn.commit()
    conn.close()

def fetch_tablets():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name, price, stock FROM tablets ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "price": float(r[2]), "stock": r[3]} for r in rows]

def find_customer_by_phone(phone):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM customers WHERE phone = ?", (phone,))
    r = cur.fetchone()
    conn.close()
    return r

def create_customer(name, phone):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO customers (name, phone) VALUES (?, ?)", (name, phone))
        conn.commit()
        cid = cur.lastrowid
    except sqlite3.IntegrityError:
        cur.execute("SELECT id FROM customers WHERE phone = ?", (phone,))
        row = cur.fetchone()
        cid = row[0] if row else None
    conn.close()
    return cid

def create_order(customer_id, items):
    conn = get_conn()
    cur = conn.cursor()
    order_date = datetime.now().isoformat(sep=' ', timespec='seconds')
    cur.execute("INSERT INTO orders (customer_id, order_date) VALUES (?, ?)", (customer_id, order_date))
    order_id = cur.lastrowid
    for tid, qty in items:
        cur.execute("INSERT INTO order_items (order_id, tablet_id, quantity) VALUES (?, ?, ?)", (order_id, tid, qty))
        cur.execute("UPDATE tablets SET stock = stock - ? WHERE id = ?", (qty, tid))
    conn.commit()
    conn.close()
    return order_id

def get_customer_orders(customer_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, order_date FROM orders WHERE customer_id = ? ORDER BY order_date DESC", (customer_id,))
    orders = []
    for oid, odate in cur.fetchall():
        cur.execute("""
            SELECT t.id, t.name, oi.quantity, t.price
            FROM order_items oi
            JOIN tablets t ON t.id = oi.tablet_id
            WHERE oi.order_id = ?
        """, (oid,))
        items = cur.fetchall()
        items_list = []
        total = 0.0
        for tid, name, qty, price in items:
            subtotal = qty * price
            items_list.append({"tablet_id": tid, "name": name, "quantity": qty, "price": float(price), "subtotal": subtotal})
            total += subtotal
        orders.append({"order_id": oid, "date": odate, "items": items_list, "total": total})
    conn.close()
    return orders

# ---------------- session/cart helpers ----------------
def init_session():
    if "cart" not in st.session_state:
        st.session_state.cart = {}
    if "pending_payment" not in st.session_state:
        st.session_state.pending_payment = None
    if "last_order_id" not in st.session_state:
        st.session_state.last_order_id = None
    # checkout inputs
    if "checkout_name" not in st.session_state:
        st.session_state.checkout_name = ""
    if "checkout_phone" not in st.session_state:
        st.session_state.checkout_phone = ""
    # lookup result
    if "lookup_orders" not in st.session_state:
        st.session_state.lookup_orders = None  # list of orders or None

def add_to_cart(tid, qty):
    if qty <= 0:
        return
    key = str(tid)
    st.session_state.cart[key] = st.session_state.cart.get(key, 0) + qty
    st.success("Added to cart")

def remove_from_cart(tid):
    st.session_state.cart.pop(str(tid), None)

def update_cart(tid, qty):
    if qty <= 0:
        remove_from_cart(tid)
    else:
        st.session_state.cart[str(tid)] = qty

def cart_details():
    tablets = {t['id']: t for t in fetch_tablets()}
    items = []
    total = 0.0
    for tid_s, qty in st.session_state.cart.items():
        tid = int(tid_s)
        t = tablets.get(tid)
        if not t:
            continue
        subtotal = qty * t['price']
        items.append({"tablet_id": tid, "name": t['name'], "qty": qty, "price": t['price'], "subtotal": subtotal, "stock": t['stock']})
        total += subtotal
    return items, total

# -------------- UPI QR generator (dynamic with amount) --------------
def generate_upi_qr_png_bytes(vpa, payee_name, amount, note=None):
    upi = f"upi://pay?pa={vpa}&pn={payee_name}&am={amount:.2f}"
    if note:
        upi += f"&tn={note}"
    try:
        import qrcode
        from PIL import Image
    except Exception as e:
        return None, f"Missing libraries to generate QR: {e}\nRun: pip install qrcode[pil] pillow"
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(upi)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read(), None

# ---------------- UI ----------------
st.set_page_config(page_title="Clinic — Order Page", layout="wide")
initialize_db()
sync_seed_tablets()
init_session()

st.title("Clinic — Order Page")
st.write("Select medicines, add to cart, then checkout. Use Lookup to view previous orders for the entered phone. Choose UPI (auto-amount QR) or Cash.")

left, right = st.columns([3, 1])

# ---------- Left: 2-column compact grid for tablets ----------
with left:
    st.header("Available Tablets / Medicines")
    tablets = fetch_tablets()
    cols_per_row = 2
    for i in range(0, len(tablets), cols_per_row):
        row = tablets[i:i+cols_per_row]
        cols = st.columns(cols_per_row)
        for col, t in zip(cols, row):
            with col:
                st.markdown(f"{t['name']}")
                st.write(f"Price: Rs {t['price']:.2f}")
                st.write(f"Stock: {t['stock']}")
                qty_key = f"qty_{t['id']}"
                btn_key = f"add_{t['id']}"
                qty = st.number_input("", min_value=0, max_value=t['stock'], value=0, step=1, key=qty_key, label_visibility="collapsed")
                if st.button("Add to cart", key=btn_key):
                    if qty <= 0:
                        st.warning("Choose quantity > 0.")
                    elif qty > t['stock']:
                        st.warning("Quantity exceeds stock.")
                    else:
                        add_to_cart(t['id'], int(qty))
                        st.rerun()

# ---------- Right: Cart & Checkout ----------
with right:
    st.header("Cart")
    items, total = cart_details()
    if not items:
        st.info("Cart is empty.")
    else:
        df = pd.DataFrame([{"Name": it['name'], "Qty": it['qty'], "Price": it['price'], "Subtotal": it['subtotal']} for it in items])
        st.table(df)
        st.markdown(f"*Total: Rs {total:.2f}*")
        if st.button("Clear cart"):
            st.session_state.cart = {}
            st.rerun()

    st.markdown("---")
    st.header("Checkout")

    # checkout inputs (not a form so Lookup and Proceed are separate)
    st.text_input("Name", key="checkout_name")
    st.text_input("Phone", key="checkout_phone", help="Enter phone then press Lookup to view previous orders", placeholder="Enter phone number")
    method = st.selectbox("Payment method", ["UPI (scan QR)", "Cash (collect)"])
    col1, col2 = st.columns([1,1])
    with col1:
        if st.button("Lookup"):
            phone = st.session_state.checkout_phone.strip()
            if not phone:
                st.warning("Enter phone before lookup.")
            else:
                found = find_customer_by_phone(phone)
                if not found:
                    st.info("No customer found with that phone.")
                    st.session_state.lookup_orders = None
                else:
                    cid = found[0]
                    orders = get_customer_orders(cid)
                    if not orders:
                        st.info("No previous orders.")
                        st.session_state.lookup_orders = []
                    else:
                        st.session_state.lookup_orders = {"customer_name": found[1], "phone": phone, "orders": orders}
    with col2:
        if st.button("Proceed to Pay"):
            phone = st.session_state.checkout_phone.strip()
            name = st.session_state.checkout_name.strip() or "Unknown"
            if not phone:
                st.error("Phone number is required.")
            elif not items:
                st.error("Cart is empty.")
            else:
                # prepare final items respecting stock
                current_tablets = {t['id']: t for t in fetch_tablets()}
                final_items = []
                for it in items:
                    tid = it['tablet_id']
                    want = it['qty']
                    tcur = current_tablets.get(tid)
                    if not tcur or tcur['stock'] <= 0:
                        st.warning(f"{it['name']} out of stock; skipped.")
                        continue
                    take = min(want, tcur['stock'])
                    final_items.append((tid, take))
                if not final_items:
                    st.error("No items available to place order.")
                else:
                    st.session_state.pending_payment = {
                        "items": final_items,
                        "total": total,
                        "name": name,
                        "phone": phone,
                        "method": method
                    }
                    st.success("Ready for payment. Follow the instructions below.")
                    st.rerun()

    # Show lookup results inside checkout area (if any)
    if st.session_state.lookup_orders:
        lo = st.session_state.lookup_orders
        st.markdown("---")
        st.subheader(f"Previous orders for {lo['customer_name']} — {lo['phone']}")
        for o in lo['orders']:
            with st.expander(f"Order {o['order_id']} — {o['date']} — Rs {o['total']:.2f}"):
                df = pd.DataFrame(o['items'])
                st.table(df[['name','quantity','price','subtotal']].rename(columns={
                    'name':'Tablet','quantity':'Qty','price':'Price','subtotal':'Subtotal'
                }))
                if st.button(f"Add items from Order {o['order_id']} to cart", key=f"reorder_{o['order_id']}"):
                    tablets_map = {t['id']: t for t in fetch_tablets()}
                    added = False
                    for it in o['items']:
                        tid = it['tablet_id']
                        want = it['quantity']
                        tcur = tablets_map.get(tid)
                        if not tcur or tcur['stock'] <= 0:
                            continue
                        take = min(want, tcur['stock'])
                        st.session_state.cart[str(tid)] = st.session_state.cart.get(str(tid), 0) + take
                        added = True
                    if added:
                        st.success("Items from previous order added to cart.")
                        st.rerun()
                    else:
                        st.info("No items available to add from that order.")

    # Payment UI / pending payment
    if st.session_state.pending_payment:
        pending = st.session_state.pending_payment
        st.markdown("---")
        st.subheader("Payment")
        st.write(f"Customer: *{pending['name']}*  |  Phone: *{pending['phone']}*")
        st.write(f"Amount: *Rs {pending['total']:.2f}*")
        if pending.get("method", "").startswith("UPI"):
            qr_bytes, err = generate_upi_qr_png_bytes(MERCHANT_VPA, MERCHANT_NAME, pending['total'], note="ClinicOrder")
            if qr_bytes:
                st.image(qr_bytes, caption=f"Scan to pay Rs {pending['total']:.2f} via UPI", use_container_width=True)
            else:
                st.error("Could not generate dynamic QR: " + str(err))
                if os.path.exists(STATIC_QR_PATH):
                    st.image(STATIC_QR_PATH, caption=f"Static QR (enter amount Rs {pending['total']:.2f} in your app)")
                else:
                    st.warning("Static QR not found. Install qrcode: pip install qrcode[pil] pillow")
            st.write("Scan the QR using any UPI app — the amount should be pre-filled.")
        else:
            st.info("Collect cash from the customer and press Confirm payment (cash collected).")

        if st.button("Confirm payment (simulate)"):
            existing = find_customer_by_phone(pending['phone'])
            if existing:
                cid = existing[0]
            else:
                cid = create_customer(pending['name'], pending['phone'])
            order_id = create_order(cid, pending['items'])
            st.success(f"Order placed. Order ID: {order_id}")
            st.session_state.last_order_id = order_id
            # clear session data for next customer
            st.session_state.cart = {}
            st.session_state.pending_payment = None
            st.session_state.lookup_orders = None
            for key in ["checkout_name", "checkout_phone"]:
                 if key in st.session_state:
                     del st.session_state[key]
            st.rerun()

    if st.session_state.last_order_id:
        st.info(f"Last order id: {st.session_state.last_order_id}")

# bottom: also keep manual view past orders (optional)
st.markdown("---")
st.header("View past orders (by phone)")
phone_query = st.text_input("Enter phone to view previous orders (bottom)", value="")
if st.button("Show orders for phone (bottom)"):
    if not phone_query.strip():
        st.warning("Enter phone number.")
    else:
        found = find_customer_by_phone(phone_query.strip())
        if not found:
            st.info("No customer found.")
        else:
            cid = found[0]
            st.subheader(f"Orders for {found[1]} ({phone_query.strip()})")
            orders = get_customer_orders(cid)
            if not orders:
                st.info("No prior orders.")
            else:
                for o in orders:
                    with st.expander(f"Order {o['order_id']} — {o['date']} — Total Rs {o['total']:.2f}"):
                        df = pd.DataFrame(o['items'])
                        st.table(df[['name','quantity','price','subtotal']].rename(columns={
                            'name':'Tablet','quantity':'Qty','price':'Price','subtotal':'Subtotal'
                        }))
