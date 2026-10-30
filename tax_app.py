import streamlit as st
import pandas as pd
import sqlite3
import os
import io
import shutil
from datetime import datetime
import altair as alt
import re
from pathlib import Path

from tax_engine import TAX_YEARS, calculate_income_tax, calculate_national_insurance

# -------------------------------------------------------------
#  Setup and folder check
# -------------------------------------------------------------
st.set_page_config(page_title="UK Self‑Employed Tax Calculator", layout="wide")
os.makedirs("backups", exist_ok=True)
conn = None

# -------------------------------------------------------------
#  Utility: safe filenames
# -------------------------------------------------------------
def safe_filename(name: str) -> str:
    safe = re.sub(r"[\\/:\*\?\"<>\|]+", "-", name)
    safe = safe.replace(" ", "_")
    return safe.strip("-_.")

# -------------------------------------------------------------
#  SQLite helpers
# -------------------------------------------------------------
def init_db():
    conn = sqlite3.connect("earnings.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS earnings(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tax_year TEXT, week INTEGER, income REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS expenses(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tax_year TEXT, category TEXT, amount REAL,
                    vat_reclaimable INTEGER DEFAULT 1)""")
    cols = [r[1] for r in c.execute("PRAGMA table_info(expenses)")]
    if "vat_reclaimable" not in cols:
        # Older databases assumed every expense carried 20% VAT; keep that,
        # except for insurance, which is VAT-exempt.
        c.execute("ALTER TABLE expenses ADD COLUMN vat_reclaimable INTEGER DEFAULT 1")
        c.execute("UPDATE expenses SET vat_reclaimable=0 WHERE category=?", ("Insurance",))
    conn.commit()
    return conn


VAT_EXEMPT_CATEGORIES = {"Insurance"}


def default_vat_reclaimable(category):
    return category not in VAT_EXEMPT_CATEGORIES


def add_income(conn, year, amount):
    if not conn:
        return
    c = conn.cursor()
    c.execute("SELECT MAX(week) FROM earnings WHERE tax_year=?", (year,))
    res = c.fetchone()[0]
    next_week = 1 if res is None else res + 1
    c.execute("INSERT INTO earnings(tax_year,week,income) VALUES(?,?,?)",
              (year, next_week, amount))
    conn.commit()


def delete_expense(conn, exp_id):
    if conn:
        conn.execute("DELETE FROM expenses WHERE id=?", (exp_id,))
        conn.commit()


def update_expense(conn, exp_id, category, amount, vat_reclaimable):
    if conn:
        conn.execute("UPDATE expenses SET category=?,amount=?,vat_reclaimable=? WHERE id=?",
                     (category, amount, int(vat_reclaimable), exp_id))
        conn.commit()

# -------------------------------------------------------------
#  Backup helpers
# -------------------------------------------------------------
def backup_database(path="earnings.db"):
    os.makedirs("backups", exist_ok=True)
    if os.path.exists(path):
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = Path("backups") / safe_filename(f"earnings_{ts}.db")
        shutil.copy2(path, dest)
        return str(dest)
    return None


def backup_csv(df, prefix):
    os.makedirs("backups", exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dest = Path("backups") / safe_filename(f"{prefix}_{ts}.csv")
    df.to_csv(dest, index=False)
    return str(dest)


def list_backups(folder="backups"):
    if not os.path.exists(folder):
        return []
    return sorted([f for f in os.listdir(folder)
                   if os.path.isfile(os.path.join(folder, f))],
                  key=lambda x: os.path.getmtime(os.path.join(folder, x)),
                  reverse=True)


def restore_database(fname, dest="earnings.db"):
    src = os.path.join("backups", fname)
    if os.path.exists(src):
        shutil.copy2(src, dest)
        return dest
    return None

# -------------------------------------------------------------
#  Streamlit UI
# -------------------------------------------------------------
st.title("📊 UK Self‑Employed Tax & NI Calculator")
year = st.selectbox("Select Tax Year:", list(TAX_YEARS.keys()))
rules = TAX_YEARS[year]
mode = st.sidebar.radio("Input Mode", ["Manual Entry", "CSV Upload", "SQLite Database"])

earnings, total_expenses, vatable_expenses = [], 0.0, 0.0
vat_registered = False
vat_rate = 0.0

# -------------------------------------------------------------
#  Manual and CSV modes
# -------------------------------------------------------------
if mode == "Manual Entry":
    n = st.sidebar.number_input("Weeks to enter", 1, 53, 4)
    for i in range(n):
        earnings.append(st.sidebar.number_input(f"Week {i+1}", 0.0, step=10.0, key=f"inc{i}"))

elif mode == "CSV Upload":
    up = st.sidebar.file_uploader("Upload CSV (one income per line)", type=["csv"])
    if up:
        df = pd.read_csv(up, header=None)
        earnings = df[0].tolist()

# -------------------------------------------------------------
#  SQLite mode (with VAT and annual option)
# -------------------------------------------------------------
elif mode == "SQLite Database":
    conn = init_db()
    st.sidebar.subheader(f"SQLite Database Mode ({year})")

    # --- VAT toggle ---
    vat_registered = st.sidebar.checkbox("I'm VAT‑registered (20% standard rate)")
    vat_rate = 0.20 if vat_registered else 0.0

    entry_mode = st.sidebar.radio("Income entry mode",
                                  ["Weekly entries", "Single annual totals"])

    if entry_mode == "Weekly entries":
        inc_val = st.sidebar.number_input("Weekly income (£)", 0.0, step=10.0)
        if st.sidebar.button("Add Weekly Income"):
            add_income(conn, year, inc_val)
            st.sidebar.success("Weekly income added.")
    else:
        st.sidebar.markdown("### Annual Income and Deductions")
        annual_income = st.sidebar.number_input("Total annual income (gross £)", 0.0, step=100.0)
        # multiple categories
        cats = [
            "Fuel", "Vehicle maintenance", "Insurance", "Phone & Internet",
            "Office supplies", "Professional services"
        ]
        annual_expenses = []
        for c in cats:
            val = st.sidebar.number_input(f"{c} (£)", 0.0, step=50.0, key=f"ann_{c}")
            if val > 0:
                annual_expenses.append((c, val))
        cust = st.sidebar.text_input("Custom category (optional)")
        if cust:
            cval = st.sidebar.number_input(f"{cust} (£)", 0.0, step=50.0, key="ann_custom")
            if cval > 0:
                annual_expenses.append((cust, cval))
        if st.sidebar.button("Add Annual Totals"):
            cur = conn.cursor()
            cur.execute("INSERT INTO earnings (tax_year, week, income) VALUES (?,?,?)",
                        (year, 0, annual_income))
            for c, v in annual_expenses:
                cur.execute("INSERT INTO expenses (tax_year, category, amount, vat_reclaimable) "
                            "VALUES (?,?,?,?)", (year, c, v, int(default_vat_reclaimable(c))))
            conn.commit()
            st.sidebar.success("Annual income and deductions added.")

    # expense entry and management
    st.sidebar.markdown("---")
    st.sidebar.subheader("Business Outgoings")
    exp_cat = st.sidebar.selectbox("Category",
                                   ["Fuel", "Vehicle maintenance", "Insurance",
                                    "Phone & Internet", "Office supplies",
                                    "Professional services", "Other (Custom)"])
    if exp_cat == "Other (Custom)":
        exp_cat = st.sidebar.text_input("Custom category name")
    exp_amt = st.sidebar.number_input("Amount (£)", 0.0, step=5.0)
    exp_vat = st.sidebar.checkbox("Includes 20% VAT", default_vat_reclaimable(exp_cat),
                                  key=f"exp_vat_{exp_cat}")
    if st.sidebar.button("Add Expense"):
        conn.execute("INSERT INTO expenses (tax_year, category, amount, vat_reclaimable) "
                     "VALUES (?,?,?,?)", (year, exp_cat, exp_amt, int(exp_vat)))
        conn.commit()
        st.sidebar.success("Expense added.")

    st.markdown("### 💸 Manage Expenses")
    exp_df = pd.read_sql("SELECT id,category,amount,vat_reclaimable FROM expenses WHERE tax_year=?",
                         conn, params=(year,))
    if exp_df.empty:
        st.info("No expenses yet.")
    else:
        for _, r in exp_df.iterrows():
            c1, c2, c5, c3, c4 = st.columns([4, 2, 1, 1, 1])
            new_cat = c1.text_input("Category", r["category"], key=f"cat{r['id']}")
            new_amt = c2.number_input("Amount(£)", 0.0, None, float(r["amount"]),
                                      5.0, key=f"amt{r['id']}")
            new_vat = c5.checkbox("VAT", bool(r["vat_reclaimable"]), key=f"vat{r['id']}")
            if c3.button("💾", key=f"save{r['id']}"):
                update_expense(conn, r["id"], new_cat, new_amt, new_vat)
                st.success("Updated.")
                st.rerun()
            if c4.button("🗑️", key=f"del{r['id']}"):
                delete_expense(conn, r["id"])
                st.warning("Deleted.")
                st.rerun()
        total_expenses = exp_df["amount"].sum()
        vatable_expenses = exp_df.loc[exp_df["vat_reclaimable"] == 1, "amount"].sum()
        by_cat = exp_df.groupby("category")["amount"].sum().reset_index()
        st.write(f"**Total Expenses:** £{total_expenses:,.2f}")
        st.bar_chart(by_cat.set_index("category"))
        pie = alt.Chart(by_cat).mark_arc().encode(
            theta="amount:Q", color="category:N", tooltip=["category", "amount"])
        st.altair_chart(pie, width="stretch")

    inc_df = pd.read_sql("SELECT week,income FROM earnings WHERE tax_year=?",
                         conn, params=(year,))
    if not inc_df.empty:
        earnings = inc_df["income"].tolist()

# -------------------------------------------------------------
#  Results section
# -------------------------------------------------------------
if earnings:
    gross = sum(earnings)
    # VAT adjustment
    if vat_registered:
        net_income = gross / (1 + vat_rate)
        vat_input = vatable_expenses - vatable_expenses / (1 + vat_rate)
        net_expenses = total_expenses - vat_input
        vat_output = gross - net_income
        vat_due = vat_output - vat_input
    else:
        net_income, net_expenses, vat_due = gross, total_expenses, 0.0

    net_profit = max(net_income - net_expenses, 0)
    tax = calculate_income_tax(net_profit, rules)
    ni = calculate_national_insurance(net_profit, rules)
    total_tax = tax + ni
    take_home = net_profit - total_tax

    st.subheader(f"💡 Results for {year}")
    st.write(f"**Gross Income:** £{gross:,.2f}")
    if vat_registered:
        st.write(f"**Net (Gross‑VAT) Income:** £{net_income:,.2f}")
        st.write(f"**Net (Gross‑VAT) Expenses:** £{net_expenses:,.2f}")
        st.write(f"**VAT to remit (20%) ≈ £{vat_due:,.2f}**")
    st.write(f"**Claimable Deductions:** £{total_expenses:,.2f}")
    st.write(f"**Taxable Profit:** £{net_profit:,.2f}")
    st.write(f"**Income Tax:** £{tax:,.2f}")
    st.write(f"**National Insurance:** £{ni:,.2f}")
    st.write(f"**Total Tax+NI:** £{total_tax:,.2f}")
    st.write(f"**Take‑home Pay:** £{take_home:,.2f}")

    summary = pd.DataFrame([{
        "Tax Year": year, "Gross": gross, "Expenses": total_expenses,
        "Taxable Profit": net_profit, "Tax": tax, "NI": ni,
        "Total Tax+NI": total_tax, "Take‑home": take_home,
        "VAT Due": vat_due
    }])
    buf = io.StringIO()
    summary.to_csv(buf, index=False)
    downloaded = st.download_button("📥 Download Tax Summary",
                                    buf.getvalue(),
                                    file_name=f"tax_summary_{safe_filename(year)}.csv",
                                    mime="text/csv")

    if downloaded and conn:
        backup_database()
        backup_csv(summary, f"tax_summary_{year}")
        st.success("💾 Backups saved in /backups")

# -------------------------------------------------------------
#  All‑Years Summary and Backups
# -------------------------------------------------------------
st.markdown("---")
if conn:
    st.header("📊 All Years Summary")
    # Aggregate each table separately; joining raw rows multiplies the sums.
    all_df = pd.read_sql("""
        SELECT e.tax_year, e.gross, IFNULL(x.deds, 0) AS deds
          FROM (SELECT tax_year, SUM(income) AS gross
                  FROM earnings GROUP BY tax_year) e
          LEFT JOIN (SELECT tax_year, SUM(amount) AS deds
                       FROM expenses GROUP BY tax_year) x
            ON e.tax_year = x.tax_year
         ORDER BY e.tax_year
    """, conn)
    if not all_df.empty:
        data = []
        for _, r in all_df.iterrows():
            yr = r["tax_year"]
            rule = TAX_YEARS.get(yr, rules)
            g, d = r["gross"] or 0, r["deds"] or 0
            p = max(g - d, 0)
            t = calculate_income_tax(p, rule)
            n = calculate_national_insurance(p, rule)
            data.append({"Year": yr, "Gross": g, "Deductions": d,
                         "Profit": p, "Tax": t, "NI": n, "Net": p - t - n})
        df = pd.DataFrame(data)
        st.dataframe(df)
        melted = df.melt(id_vars="Year",
                         value_vars=["Gross", "Deductions", "Net"],
                         var_name="Metric", value_name="Value")
        chart = alt.Chart(melted).mark_bar().encode(
            x="Year:N", y="Value:Q", color="Metric:N",
            tooltip=["Year","Metric","Value"]
        )
        st.altair_chart(chart, width="stretch")

st.header("💾 Backup Management")
files = list_backups()
if not files:
    st.info("No backups yet. They’re created when you download a summary.")
else:
    choice = st.selectbox("Select backup", files)
    pth = os.path.join("backups", choice)
    st.write(f"**File:** {choice}  |  Size: {os.path.getsize(pth)/1024:.1f} KB")
    with open(pth, "rb") as f:
        st.download_button("⬇️ Download Backup", data=f, file_name=choice)
    if st.button("♻️ Restore This Backup"):
        restore_database(choice)
        st.warning("Database restored. Restart the app to reload.")