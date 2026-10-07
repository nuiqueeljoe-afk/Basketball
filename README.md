# Court District

Court District is a basketball footwear store and inventory platform built for a small retail business. It helps a shop owner manage shoe inventory while giving customers a clean storefront to browse and buy premium court-ready sneakers.

## Problem being solved

Many small basketball shoe retailers still rely on spreadsheets or manual note-taking to manage stock, pricing, and product information. This leads to pricing errors, stock confusion, and slow restocking decisions. Court District centralizes the catalog and gives the admin a clear dashboard to manage products.

## Features

- Public landing page with business overview and call-to-action
- Customer registration and login
- Hashed passwords stored in SQLite
- Admin and customer role separation
- Complete CRUD for shoe inventory
- Customer cart and wishlist with item counts in the navigation
- Checkout with stock validation, saved order details, and customer order history
- Demo payment options: cash on delivery or pay at store pickup (no payment gateway)
- Input validation and confirmation before delete
- Persistent data storage with SQLite
- Responsive page layout for mobile and desktop

## Tech stack

- Python 3.14
- Flask 3.1
- SQLite 3
- Jinja templates
- Flask-WTF
- pytest

## Setup and run

1. Open a terminal in the project folder.
2. Install dependencies:
   ```bash
   python -m pip install --break-system-packages -r requirements.txt
   ```
3. Initialize the database:
   ```bash
   flask --app app init-db
   ```
4. Start the app:
   ```bash
   flask --app app run
   ```
5. Open the app in a browser at http://127.0.0.1:5000/

## Default admin account

- Email: admin@courtdistrict.local
- Password: CourtDistrict!2026

## Sample customer account

Register a new account through the app, or use a sample account like:

- Name: Jamie Lee
- Email: jamie@example.test
- Password: StrongPass123

## AI tools used

- GitHub Copilot: scaffolded app structure, generated Flask routes, and assisted with the landing page and template design.
- VS Code Python tooling: verified environment setup and tested the app in the editor.

## Notes

- Passwords are hashed using Werkzeug before storage.
- The admin user can create, edit, delete, and view all shoe entries.
- Regular users can register, log in, and update their own account details.
- Product inventory is stored in a local SQLite database, so it persists between restarts.
