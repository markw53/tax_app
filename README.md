# 📊 UK Self‑Employed Tax & NI Calculator

This is a personal Streamlit web app designed for **self‑employed UK delivery drivers** (for example, Amazon Flex drivers) who need to track weekly income, claimable deductions, and calculate **Income Tax** and **National Insurance (Class 2 & 4)** liabilities each tax year.

---

## 🧾 MTD records app (`mtd_app/`)

A multi-user web app for keeping Making Tax Digital records: self-employment and UK property income, with jointly owned property split between owners automatically. It doesn't talk to HMRC yet; that's the next phase.

**What it does**

- A separate login for each taxpayer; you only see your own records and properties you own
- Transactions in HMRC's categories (SA103F / SA105), entered by hand or imported from CSV
- Joint property: enter the full amounts once, and each owner gets their share
- Mileage log, with the simplified 45p/25p mileage allowance if you choose that method
- Quarterly totals with update deadlines, plus an estimated tax bill for the year

**Setup**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/alembic upgrade head          # creates mtd.db

.venv/bin/python -m mtd_app.cli create-user --email you@example.com --name "Your Name"
.venv/bin/python -m mtd_app.cli add-source --email you@example.com --type self-employment \
    --name "Delivery driving" --vehicle-method simplified
.venv/bin/python -m mtd_app.cli add-property --name "Rental flat" \
    --owner you@example.com:50 --owner partner@example.com:50

.venv/bin/uvicorn mtd_app.main:app      # then open http://localhost:8000
```

Run `python -m mtd_app.cli --help` for the other commands (`set-password`, `list`). After changing `mtd_app/models.py`, create a migration with `alembic revision --autogenerate -m "..."`.

**Configuration:** `MTD_DATABASE_URL` (default `sqlite:///mtd.db`) and `MTD_SECRET_KEY` (otherwise generated once into `.secret_key`). Back up `mtd.db`: HMRC requires you to keep records for at least 5 years after the 31 January filing deadline.

---

## 📊 Phase 1 calculator (`tax_app.py`)

## 🚀 Features

✅ **Multi‑Year Support**  
Choose and save data for any tax year (2022/23 to 2026/27). Tax rules live in `tax_engine/rules.py`; Class 2 NI is only charged up to 2023/24, after which it was abolished.  

✅ **Variable Weekly Income Tracking**  
Add income entries manually, via CSV upload, or directly into a local SQLite database.  

✅ **Claimable Deductions**  
Enter and categorise outgoings (e.g. fuel, insurance, maintenance, etc.) that reduce your taxable profit.  

✅ **Persistent Local Storage (SQLite)**  
Your data is stored locally in a lightweight `earnings.db` file, so you can reopen and add new entries at any time.  

✅ **Year‑End Rollover**  
Start a fresh tax year easily while preserving previous years’ records.  

✅ **Analytics & Visualisations**  
Automatic charts for:

- Income vs deductions by tax year

- Expense breakdown by category  

✅ **Export to CSV**  
Download detailed yearly summaries or income logs for your records or your accountant.

> Figures are estimates for rest-of-UK taxpayers (Scottish income tax rates aren't supported yet).

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest
```

---

## 🧰 Requirements

- Python 3.9+
- Streamlit
- Pandas
- Altair

Install dependencies:

```bash
pip install -r requirements.txt

🏃‍♂️ Run Locally
Clone the repository:
bash
git clone https://github.com/YOURUSERNAME/uk-tax-calculator.git
cd uk-tax-calculator
Install dependencies:
bash
pip install -r requirements.txt
Run the app:
bash
streamlit run tax_app.py
Open the displayed local URL (e.g. http://localhost:8501).
☁️ Deploy on Streamlit Cloud
Push this repository to GitHub.
Log in at https://share.streamlit.io.
Click "New App", choose your repo and branch.
Select the main file: tax_app.py
Click Deploy 🚀
Your app will be live at:

text
https://yourusername-uk-tax-calculator.streamlit.app
📊 Screenshots (optional)
You can add screenshots like this later:

markdown
![Screenshot: Main Dashboard](images/dashboard.png)
![Screenshot: All Years Summary](images/summary.png)
🧾 Data & Storage Notes
All data is stored in earnings.db (SQLite database).
You can export and re‑import CSV backups anytime.
When deployed on Streamlit Cloud, ephemeral storage resets when the app sleeps — so export your data if you need long‑term retention.

👨‍💻 Built With
Streamlit
Pandas
Altair
⚖️ Disclaimer
This tool is a personal tax calculator.
Always verify calculations with official HMRC tools or an accountant for final submissions.
Tax thresholds and rates are based on UK government guidance for the selected tax years.

📬 License
MIT License © 2025 Mark Workman

text

---
