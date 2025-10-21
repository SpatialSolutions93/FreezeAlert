#!/usr/bin/env python3
import os
import json
import requests
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configuration
LAT = 45.0411  # Scotts Mills, Oregon
LON = -122.6700
ZIP_CODE = "97375"
LOCATION_NAME = "Scotts Mills, Oregon"

# Alert tracking file to persist state between runs
ALERT_HISTORY_FILE = "alert_history.json"

def get_weather_forecast():
    """Get weather forecast from National Weather Service API"""
    try:
        # Get the forecast office and grid coordinates
        point_url = f"https://api.weather.gov/points/{LAT},{LON}"
        response = requests.get(point_url, headers={"User-Agent": "FreezeAlert/1.0"})
        response.raise_for_status()
        point_data = response.json()

        # Get the hourly forecast
        forecast_url = point_data["properties"]["forecastHourly"]
        forecast_response = requests.get(forecast_url, headers={"User-Agent": "FreezeAlert/1.0"})
        forecast_response.raise_for_status()
        forecast_data = forecast_response.json()

        return forecast_data["properties"]["periods"]
    except Exception as e:
        print(f"Error fetching weather data: {e}")
        # Fallback to OpenMeteo (no API key required)
        return get_openmeteo_forecast()

def get_openmeteo_forecast():
    """Fallback weather provider - OpenMeteo (no API key required)"""
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": LAT,
            "longitude": LON,
            "hourly": "temperature_2m",
            "temperature_unit": "fahrenheit",
            "timezone": "America/Los_Angeles",
            "forecast_days": 7
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        # Convert to NWS-like format
        periods = []
        for i, (time_str, temp) in enumerate(zip(data["hourly"]["time"], data["hourly"]["temperature_2m"])):
            if i >= 168:  # Limit to 7 days
                break
            periods.append({
                "startTime": time_str,
                "temperature": temp,
                "name": f"Hour {i+1}"
            })
        return periods
    except Exception as e:
        print(f"Error fetching OpenMeteo data: {e}")
        return []

def load_alert_history():
    """Load the history of alerts sent"""
    if os.path.exists(ALERT_HISTORY_FILE):
        with open(ALERT_HISTORY_FILE, 'r') as f:
            return json.load(f)
    return {
        "first_frost_alerted": None,
        "second_frost_alerted": None,
        "extended_freeze_alerts": []
    }

def save_alert_history(history):
    """Save the alert history"""
    with open(ALERT_HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)

def check_freezing_conditions(forecast_periods):
    """Analyze forecast for freezing conditions"""
    alerts = []
    history = load_alert_history()
    current_year = datetime.now().year

    # Check for frost events (32°F or below)
    frost_events = []
    extended_freezes = []

    i = 0
    while i < len(forecast_periods):
        period = forecast_periods[i]
        temp = period.get("temperature", 100)

        # Handle different forecast formats
        if isinstance(temp, dict):
            temp = temp.get("value", 100)

        time_str = period.get("startTime", "")

        if temp <= 32:
            # Found a freezing period
            freeze_start = i
            freeze_hours = 1

            # Check how long the freeze lasts
            j = i + 1
            while j < len(forecast_periods):
                next_temp = forecast_periods[j].get("temperature", 100)
                if isinstance(next_temp, dict):
                    next_temp = next_temp.get("value", 100)

                if next_temp <= 32:
                    freeze_hours += 1
                    j += 1
                else:
                    break

            # Record the freeze event
            freeze_event = {
                "start_time": time_str,
                "duration_hours": freeze_hours,
                "min_temp": min(
                    forecast_periods[k].get("temperature", 100) if not isinstance(forecast_periods[k].get("temperature", 100), dict)
                    else forecast_periods[k].get("temperature", {}).get("value", 100)
                    for k in range(freeze_start, min(freeze_start + freeze_hours, len(forecast_periods)))
                )
            }

            if freeze_hours > 1:
                extended_freezes.append(freeze_event)

            if not frost_events:  # First frost of the forecast period
                frost_events.append(freeze_event)
            elif len(frost_events) == 1:  # Could be second frost
                # Check if this is at least 24 hours after the first
                try:
                    first_time = datetime.fromisoformat(frost_events[0]["start_time"].replace("Z", "+00:00"))
                    current_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                    if (current_time - first_time).days >= 1:
                        frost_events.append(freeze_event)
                except:
                    frost_events.append(freeze_event)

            # Skip ahead past this freeze event
            i = j
        else:
            i += 1

    # Generate alerts based on findings

    # First frost alert
    if frost_events and not history.get("first_frost_alerted"):
        event = frost_events[0]
        alerts.append({
            "type": "FIRST FROST",
            "message": f"First frost of the season expected!\nTime: {event['start_time']}\nMinimum temperature: {event['min_temp']}°F\nDuration: {event['duration_hours']} hours",
            "event": event
        })
        history["first_frost_alerted"] = event["start_time"]

    # Second frost alert
    if len(frost_events) > 1 and not history.get("second_frost_alerted"):
        event = frost_events[1]
        alerts.append({
            "type": "SECOND FROST",
            "message": f"Second frost expected!\nTime: {event['start_time']}\nMinimum temperature: {event['min_temp']}°F\nDuration: {event['duration_hours']} hours",
            "event": event
        })
        history["second_frost_alerted"] = event["start_time"]

    # Extended freeze alerts (more than 1 hour below freezing)
    for freeze in extended_freezes:
        # Check if we've already alerted for this specific freeze
        alert_key = f"{freeze['start_time']}_{freeze['duration_hours']}"
        if alert_key not in history.get("extended_freeze_alerts", []):
            alerts.append({
                "type": "EXTENDED FREEZE",
                "message": f"Extended freeze warning!\nStart time: {freeze['start_time']}\nDuration: {freeze['duration_hours']} hours below freezing\nMinimum temperature: {freeze['min_temp']}°F",
                "event": freeze
            })
            if "extended_freeze_alerts" not in history:
                history["extended_freeze_alerts"] = []
            history["extended_freeze_alerts"].append(alert_key)

    # Clean up old extended freeze alerts (older than 14 days)
    if "extended_freeze_alerts" in history:
        cutoff = datetime.now() - timedelta(days=14)
        history["extended_freeze_alerts"] = [
            alert for alert in history["extended_freeze_alerts"]
            if not alert or datetime.fromisoformat(alert.split("_")[0].replace("Z", "+00:00")) > cutoff
        ]

    save_alert_history(history)
    return alerts

def send_email_alert(alerts, min_temp_48h=None, min_temp_7d=None):
    """Send email alerts using Gmail SMTP"""
    # Get email credentials from environment variables
    sender_email = os.environ.get("SENDER_EMAIL")
    sender_password = os.environ.get("SENDER_PASSWORD")  # App-specific password
    recipient_email = os.environ.get("RECIPIENT_EMAIL", sender_email)

    if not sender_email or not sender_password:
        print("Email credentials not configured. Set SENDER_EMAIL and SENDER_PASSWORD environment variables.")
        if alerts:
            print("Alerts that would have been sent:")
            for alert in alerts:
                print(f"\n{alert['type']}:")
                print(alert['message'])
        return

    # Create message
    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = recipient_email

    # Determine subject based on whether there are alerts
    if alerts:
        msg["Subject"] = f"⚠️ FREEZE ALERT - {LOCATION_NAME}"
    else:
        msg["Subject"] = f"✓ Weather Check OK - {LOCATION_NAME}"

    # Build email body
    body = f"Weather report for {LOCATION_NAME} ({ZIP_CODE}):\n\n"

    if alerts:
        for alert in alerts:
            body += f"{'='*50}\n"
            body += f"{alert['type']}\n"
            body += f"{'='*50}\n"
            body += alert['message']
            body += f"\n\n"
    else:
        body += "✓ No freezing conditions detected in the forecast.\n\n"
        if min_temp_48h is not None:
            body += f"Minimum temp next 48 hours: {min_temp_48h:.0f}°F\n"
        if min_temp_7d is not None:
            body += f"Minimum temp next 7 days: {min_temp_7d:.0f}°F\n"
        body += "\nYour freeze alert system is working properly.\n"

    body += f"\n{'='*50}\n"
    body += f"This is an automated alert from your freeze monitoring system.\n"
    body += f"Location: {LOCATION_NAME} (Lat: {LAT}, Lon: {LON})\n"
    body += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} PST"

    msg.attach(MIMEText(body, "plain"))

    # Send email
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        print(f"Email sent successfully to {recipient_email}")
        for alert in alerts:
            print(f"- {alert['type']}")
    except Exception as e:
        print(f"Error sending email: {e}")
        print("\nAlerts that failed to send:")
        for alert in alerts:
            print(f"\n{alert['type']}:")
            print(alert['message'])

def simulate_test_alerts(test_mode):
    """Simulate alerts for testing"""
    test_alerts = []

    if test_mode in ["frost1", "all"]:
        test_alerts.append({
            "type": "FIRST FROST",
            "message": f"TEST ALERT - First frost of the season expected!\nTime: Tonight at 11 PM\nMinimum temperature: 28°F\nDuration: 3 hours\n\nThis is a TEST message to verify SMS delivery is working.",
            "event": {"start_time": "TEST", "duration_hours": 3, "min_temp": 28}
        })

    if test_mode in ["frost2", "all"]:
        test_alerts.append({
            "type": "SECOND FROST",
            "message": f"TEST ALERT - Second frost expected!\nTime: Tomorrow at 2 AM\nMinimum temperature: 30°F\nDuration: 2 hours\n\nThis is a TEST message to verify SMS delivery is working.",
            "event": {"start_time": "TEST", "duration_hours": 2, "min_temp": 30}
        })

    if test_mode in ["extended_freeze", "all"]:
        test_alerts.append({
            "type": "EXTENDED FREEZE",
            "message": f"TEST ALERT - Extended freeze warning!\nStart time: Tonight at 9 PM\nDuration: 6 hours below freezing\nMinimum temperature: 25°F\n\nThis is a TEST message to verify SMS delivery is working.",
            "event": {"start_time": "TEST", "duration_hours": 6, "min_temp": 25}
        })

    return test_alerts

def main():
    """Main function to check weather and send alerts"""
    import sys

    # Check for test mode
    test_mode = None
    if len(sys.argv) > 1:
        test_mode = sys.argv[1].lower()
        if test_mode in ["frost1", "frost2", "extended_freeze", "all"]:
            print(f"Running in TEST MODE: {test_mode}")
            alerts = simulate_test_alerts(test_mode)
            if alerts:
                print(f"Sending {len(alerts)} TEST alert(s)")
                send_email_alert(alerts)
            return
        else:
            print(f"Invalid test mode: {test_mode}")
            print("Valid options: frost1, frost2, extended_freeze, all")
            return

    print(f"Checking weather for {LOCATION_NAME}...")

    # Get weather forecast
    forecast = get_weather_forecast()

    if not forecast:
        print("Unable to retrieve weather forecast")
        return

    print(f"Retrieved {len(forecast)} hours of forecast data")

    # Check for freezing conditions
    alerts = check_freezing_conditions(forecast)

    # Calculate minimum temperatures for status report
    min_temp_48h = None
    min_temp_7d = None

    if forecast:
        temps_48h = []
        temps_7d = []
        for i, period in enumerate(forecast):
            temp = period.get("temperature", None)
            if isinstance(temp, dict):
                temp = temp.get("value", None)
            if temp is not None and isinstance(temp, (int, float)):
                temps_7d.append(temp)
                if i < 48:
                    temps_48h.append(temp)

        if temps_48h:
            min_temp_48h = min(temps_48h)
        if temps_7d:
            min_temp_7d = min(temps_7d)

    # Always send an email - either with alerts or status update
    if alerts:
        print(f"Found {len(alerts)} alert(s) to send")
    else:
        print("No freezing conditions detected - sending status update")

    send_email_alert(alerts, min_temp_48h, min_temp_7d)

    # Print next 48 hours summary for logging
    print("\nNext 48 hours temperature summary:")
    for i, period in enumerate(forecast[:48]):
        if i % 6 == 0:  # Every 6 hours
            temp = period.get("temperature", "N/A")
            if isinstance(temp, dict):
                temp = temp.get("value", "N/A")
            time_str = period.get("startTime", period.get("name", f"Hour {i}"))
            print(f"  {time_str}: {temp}°F")

if __name__ == "__main__":
    main()