# StockSense

**AI-assisted inventory management for small shops**

StockSense is a lightweight inventory-management prototype that helps small shopkeepers manage products, record deliveries, and review stock changes through a simple web dashboard.

## Features

- Add and view products with current stock quantities
- Search the inventory by product name
- Record deliveries and update stock after confirmation
- Review delivery history
- Adjust stock and record a reason for the change
- Use an AI-assisted scanner to suggest item names and quantities from delivery evidence, when the configured model supports it
- Review and correct AI suggestions before saving inventory changes

## Tech Stack

- **Backend:** Python, FastAPI
- **Database:** SQLite
- **Frontend:** HTML, CSS, JavaScript
- **AI:** Meta AI model integration (requires verified API access and configuration)

## How It Works

1. Add products and their starting quantities.
2. Record a delivery manually or request an AI-assisted scan.
3. Review and correct the suggested item names and quantities.
4. Confirm the delivery to save it and update inventory.
5. Check current stock and delivery history from the dashboard.

AI-generated values are suggestions only. Stock is updated after user confirmation.

## Project Structure

```text
StockSense/
├── main.py
├── requirements.txt
├── .env
└── static/
    └── index.html