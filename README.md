# 📊 UK Self‑Employed Tax & NI Calculator

This is a personal Streamlit web app designed for **self‑employed UK delivery drivers** (for example, Amazon Flex drivers) who need to track weekly income, claimable deductions, and calculate **Income Tax** and **National Insurance (Class 2 & 4)** liabilities each tax year.

---

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
