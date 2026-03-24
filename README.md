# QuickPOS - Point of Sale System

A comprehensive POS (Point of Sale) system built with Python Flask, SQLite, and modern HTML/CSS/JavaScript.

## Features

### 1. User Interface (Front-End)
- Modern responsive web interface
- Product search and barcode input
- Shopping cart with quantity adjustment
- Discount and tax application
- Checkout screen with receipt preview

### 2. Product Management
- Add, edit, delete products
- Product categorization and pricing
- Barcode management
- Search and filter products

### 3. Inventory Management
- Real-time stock tracking
- Automatic stock deduction after sale
- Low stock alerts
- Stock replenishment and adjustments
- Inventory activity log

### 4. Sales Processing
- Create sales with multiple items
- Calculate totals with discounts and taxes
- Generate receipts automatically

### 5. Payment Processing
- Cash, Mobile Money, and Card payments
- Change calculation for cash payments
- Payment record storage

### 6. Customer Management
- Customer registration
- Purchase history tracking
- Loyalty points system

### 7. Database (SQLite)
- Tables: Users, Products, Customers, Sales, Sales_Items, Inventory_Log, Payments, Transaction_Logs

### 8. Reporting & Analytics
- Daily sales reports
- Weekly sales reports with charts
- Product performance reports
- Inventory reports
- Cashier performance reports

### 9. User Authentication & Roles
- Login/Logout with session management
- Password hashing (SHA-256)
- Role-based access: Admin, Manager, Cashier

### 10. Receipt Generation
- Store name, date/time, items, taxes, totals, payment method
- Print-friendly layout

### 11. Backup & Data Recovery
- Database backup creation
- Restore from backup
- Transaction logs for audit trail

---

## Setup Instructions

### Prerequisites
- Python 3.8 or higher
- pip (Python package manager)

### Installation

1. **Navigate to the project folder:**
   ```bash
   cd pos-system
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the application:**
   ```bash
   python app.py
   ```

4. **Open your browser and go to:**
   ```
   http://127.0.0.1:5000
   ```

### Default Login
- **Username:** `admin`
- **Password:** `admin123`

---

## Project Structure

```
pos-system/
├── app.py                  # Main Flask application (backend)
├── requirements.txt        # Python dependencies
├── README.md              # This file
├── database/              # SQLite database folder (auto-created)
│   ├── pos.db             # Main database (auto-created on first run)
│   └── backups/           # Backup files
├── static/
│   └── css/
│       └── style.css      # Main stylesheet
└── templates/
    ├── base.html          # Base layout template
    ├── login.html         # Login page
    ├── dashboard.html     # Dashboard with stats and charts
    ├── pos.html           # Point of Sale screen
    ├── receipt.html       # Receipt template
    ├── products.html      # Product management
    ├── inventory.html     # Inventory management
    ├── customers.html     # Customer management
    ├── users.html         # User management (Admin only)
    ├── reports.html       # Reports & Analytics
    └── backup.html        # Backup & Recovery
```

## Architecture

This system follows a **three-tier architecture**:
1. **Presentation Layer:** HTML/CSS/JavaScript templates
2. **Application Layer:** Flask Python backend (business logic)
3. **Data Layer:** SQLite database

## Technologies Used
- **Backend:** Python, Flask
- **Frontend:** HTML5, CSS3, JavaScript
- **Database:** SQLite
- **Charts:** Chart.js
- **Icons:** Font Awesome 6
- **Fonts:** DM Sans, JetBrains Mono (Google Fonts)

## User Roles & Permissions

| Feature              | Admin | Manager | Cashier |
|---------------------|-------|---------|---------|
| Dashboard           | ✅    | ✅      | ✅      |
| Point of Sale       | ✅    | ✅      | ✅      |
| View Products       | ✅    | ✅      | ✅      |
| Add/Edit Products   | ✅    | ✅      | ❌      |
| Delete Products     | ✅    | ❌      | ❌      |
| Inventory           | ✅    | ✅      | ✅      |
| Adjust Stock        | ✅    | ✅      | ❌      |
| Customers           | ✅    | ✅      | ✅      |
| Reports             | ✅    | ✅      | ❌      |
| User Management     | ✅    | ❌      | ❌      |
| Backup & Recovery   | ✅    | ❌      | ❌      |
