# Freeze Alert System

Automated weather monitoring system that sends email alerts for freezing temperatures in Scotts Mills, Oregon.

## Features

- **First Frost Alert**: Notifies you of the season's first frost
- **Second Frost Alert**: Alerts for the second frost event
- **Extended Freeze Warning**: Alerts when temperature will be below freezing for more than 1 hour
- Runs automatically twice daily at 6 AM and 6 PM PST via GitHub Actions
- Uses National Weather Service API (with OpenMeteo fallback)

## Setup Instructions

1. **Fork or create a new GitHub repository** with this code

2. **Configure GitHub Secrets** (Settings → Secrets and variables → Actions):

   - `SENDER_EMAIL`: ----------@gmail.com
   - `SENDER_PASSWORD`: Gmail App Password (not regular password)
   - `RECIPIENT_EMAIL`: ----------@vtext.com (for Verizon SMS alerts)

3. **Get Gmail App Password**:

   - Go to https://myaccount.google.com/security
   - Enable 2-factor authentication if not already enabled
   - Select "2-Step Verification" → "App passwords"
   - Generate a new app password for "Mail"
   - Use this password for `SENDER_PASSWORD`

4. **Enable GitHub Actions**:
   - Go to the Actions tab in your repository
   - Enable workflows if prompted

## Manual Testing

To test the system manually:

1. Go to Actions tab
2. Select "Freeze Alert Check" workflow
3. Click "Run workflow" → "Run workflow"

## Email to Text Setup (Optional)

To receive alerts as text messages:

- Most carriers offer email-to-SMS gateways
- Common formats:
  - Verizon: phonenumber@vtext.com
  - AT&T: phonenumber@txt.att.net
  - T-Mobile: phonenumber@tmomail.net
  - Sprint: phonenumber@messaging.sprintpcs.com
- Set `RECIPIENT_EMAIL` to your carrier's email-to-SMS address

## How It Works

1. Checks weather forecast twice daily (6 AM and 6 PM PST)
2. Analyzes next 7 days for freezing conditions
3. Sends email alerts when:
   - First frost of season is detected
   - Second frost occurs (at least 24 hours after first)
   - Temperature will be below 32°F for more than 1 hour
4. Maintains history to avoid duplicate alerts

## Location

Currently configured for:

- **Location**: Scotts Mills, Oregon
- **ZIP**: 97375
- **Coordinates**: 45.0411°N, 122.6700°W

To change location, edit the `LAT`, `LON`, and `LOCATION_NAME` variables in `freeze_alert.py`.

## Alert History

The system maintains an `alert_history.json` file to track sent alerts and prevent duplicates. This file is automatically updated and committed by GitHub Actions.
