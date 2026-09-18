# FreshFold Laundry

**Web-Based Laundry Shop Management System with QR Code-Based Laundry Tracking**

A small Flask + SQLite college project for managing customers, laundry orders, payments, services, receipts, and customer-facing QR tracking.

## Included

- Admin/staff login and logout
- Dashboard totals for customers, orders, pending, completed, ready for pickup, and sales
- Customer CRUD, search, profiles, and order history
- Laundry orders with automatic `weight x price/kg = total` and balance calculation
- Cash, GCash, and Bank Transfer payment methods
- Unpaid, Partially Paid, and Paid payment statuses
- Service price management
- QR code generation for every order
- Mobile-friendly public tracking page
- Status history: Received -> Washing -> Drying -> Folding -> Ready for Pickup -> Completed
- Print-friendly receipt containing the order QR code
- Order filters by text, laundry status, payment status, and received date

## Run locally

1. Open PowerShell in this folder.
2. Create and activate a virtual environment (recommended):

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Start the application:

   ```powershell
   python app.py
   ```

5. Open `http://127.0.0.1:5000` on the computer.

## Scan QR codes from a phone

The phone and the computer running Flask must be connected to the same Wi-Fi network. Find the computer's local IPv4 address with `ipconfig`, then open the app using that address, for example:

```text
http://192.168.1.25:5000
```

QR codes generated from a localhost session automatically use the computer's detected LAN address when possible. You can also set an exact base URL before starting Flask:

```powershell
$env:TRACKING_BASE_URL = "http://192.168.1.25:5000"
python app.py
```

If Windows Firewall prompts about Python, allow access on private networks. Do not use `localhost` or `127.0.0.1` on the phone because those addresses refer to the phone itself.

The SQLite database is created automatically as `laundry.db` on first run, with sample services, customers, and users.

## Demo accounts

- Admin: `admin` / `admin123`
- Staff: `staff` / `staff123`

## Presentation workflow

Login -> Dashboard -> Add customer -> New laundry order -> Print receipt / download QR -> Open the tracking link -> Update status from the order page.

The QR link uses the URL of the running application. For a phone on the same Wi-Fi network, run Flask on the computer's local IP and use that reachable URL when generating QR codes.
