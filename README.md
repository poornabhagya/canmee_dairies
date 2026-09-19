# Canmee Dairies - Django Milk Management System

This project converts the existing `2026.xlsm` workflow into a normalized, production-style Django + MySQL system.

## Workbook process studied (`2026.xlsm`)

Main process mapped:
- `Roots` -> `Route` master
- `Center` + `Collection` -> daily point-level milk collection entries
- `Day Summary`, `Root Summary`, `Point Summary` -> reporting/aggregation views
- Buyer sheets (`Pelwatte`, `Nestle`, etc.) + dispatch sheets -> `MilkDistribution`
- Quality columns (`Fat`, `SNF`, `LR`, `Alcohol`, `Acidity`, `KQ`) -> `MilkFactor`

## Features implemented

- Authentication: login/logout/password change/reset
- Role-based access via Django Groups + permissions page
- Master data: `Route`, `CollectionPoint`, `Buyer`, `Farmer`
- Milk collection with auto liters (`liters = kg * 0.97`)
- Unique daily entry guard (`date + route + collection_point`)
- Milk factors model and CRUD
- Milk dispatch/distribution to buyers
- Summary reports + filters
- Export: CSV, Excel, PDF
- Import: CSV/Excel for collection and distribution
- Bootstrap 5, DataTables, SweetAlert, Chart.js dashboard
- Audit fields (`created_at`, `updated_at`, `is_deleted`)
- Initial migrations included

## Tech stack

- Django 5+
- MySQL
- pandas/openpyxl
- reportlab

## Setup (local)

1. Create and activate virtual env
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Edit `canmee_dairies/settings.py` for MySQL credentials, `SECRET_KEY`, and (when not DEBUG) `ALLOWED_HOSTS`. Use a parent folder name ending in `dev` or `prod` to match database host blocks, same idea as myprestige-style layouts.
4. Create MySQL database:
   - `CREATE DATABASE canmee_dairies CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`
5. Run migrations:
   - `python manage.py migrate`
6. Create superuser:
   - `python manage.py createsuperuser`
7. (Optional) Load sample data:
   - `python manage.py loaddata fixtures/sample_data.json`
8. Create groups in admin:
   - `Admin` (all permissions)
   - `Data Entry User` (add/change on data models)
   - `Viewer` (view permissions only)
9. Run server:
   - `python manage.py runserver`

## Import file columns expected

### Collections import
- `date`, `route_id`, `collection_point_id`, `kg`

### Distributions import
- `date`, `buyer_id`, `route_id`, `collection_point_id`, `dispatch_no`, `lorry_no`, `driver`, `kg`, `fat`, `snf`, `lr`, `alcohol`, `acidity`

## Apps

- `authentication`
- `masters`
- `collections`
- `dispatch`
- `reports`
