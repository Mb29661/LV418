# Perifal LV-418 Dashboard

Web dashboard for monitoring and controlling Perifal LV-418 heat pump via the Warmlink cloud API.

## Features

- Real-time monitoring of temperatures, COP, and power consumption
- Historical data with charts (cloud + local database)
- Wood heating detection
- AT compensation curve visualization
- Control panel with password protection
- Background data logging every 10 minutes

## Local Setup

1. Clone the repository
2. Create `.env` file:
```
PERIFAL_USERNAME=your_email
PERIFAL_PASSWORD=your_password
PERIFAL_DEVICE_CODE=your_device_code
```
3. Install dependencies:
```bash
pip install -r requirements.txt
```
4. Run:
```bash
python dashboard.py
```
5. Open http://localhost:5051

## Railway Deployment

1. Create new project on [Railway](https://railway.app)
2. Connect this GitHub repository
3. Add PostgreSQL database to the project
4. Set environment variables:
   - `PERIFAL_USERNAME`
   - `PERIFAL_PASSWORD`
   - `PERIFAL_DEVICE_CODE`
5. Deploy!

The `DATABASE_URL` is automatically set by Railway when you add PostgreSQL.

## API Endpoints

- `GET /` - Dashboard UI
- `GET /api/status` - Current heat pump status
- `GET /api/history?hours=72` - Historical data from cloud
- `GET /api/local-history?hours=168` - Historical data from local database
- `GET /api/energy?hours=72` - Energy consumption data
- `GET /api/db-stats` - Database statistics
- `POST /api/control` - Control heat pump parameters

## Tech Stack

- Flask (Python web framework)
- Chart.js (charts)
- SQLite (local) / PostgreSQL (Railway)
- Warmlink Cloud API
