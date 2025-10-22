#!/usr/bin/env python3
import os
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import smtplib
from email.mime.text import MIMEText

# Configuration
LAT = 45.0411  # Scotts Mills, Oregon
LON = -122.6700
ZIP_CODE = "97375"
LOCATION_NAME = "Scotts Mills, Oregon"

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
SCHEDULED_HOURS = {6, 18}

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
            "message": f"First frost warning\n{event['start_time']}\nLow: {event['min_temp']}F\nDuration: {event['duration_hours']}hrs",
            "event": event
        })
        history["first_frost_alerted"] = event["start_time"]

    # Second frost alert
    if len(frost_events) > 1 and not history.get("second_frost_alerted"):
        event = frost_events[1]
        alerts.append({
            "type": "SECOND FROST",
            "message": f"Second frost warning\n{event['start_time']}\nLow: {event['min_temp']}F\nDuration: {event['duration_hours']}hrs",
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
                "message": f"Extended freeze\n{freeze['start_time']}\nLow: {freeze['min_temp']}F\nDuration: {freeze['duration_hours']}hrs",
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

def get_pacific_now():
    """Return the current time in Pacific time."""
    return datetime.now(PACIFIC_TZ)

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

    # Build email body
    body_lines = []

    if alerts:
        for alert in alerts:
            body_lines.append(alert['type'])
            body_lines.append(alert['message'])
            body_lines.append("")
        if body_lines and body_lines[-1] == "":
            body_lines.pop()
    else:
        body_lines.append("No freeze detected")
        body_lines.append("")
        details = []
        if min_temp_48h is not None:
            details.append(f"48hr low: {min_temp_48h:.0f}F")
        if min_temp_7d is not None:
            details.append(f"7day low: {min_temp_7d:.0f}F")
        if details:
            body_lines.extend(details)

    pacific_now = get_pacific_now()
    if not body_lines or body_lines[-1] != "":
        body_lines.append("")
    body_lines.append(LOCATION_NAME)
    body_lines.append(pacific_now.strftime('%m/%d %I:%M%p %Z'))

    body = "\n".join(body_lines)

    msg = MIMEText(body, "plain")
    msg["From"] = sender_email
    msg["To"] = recipient_email

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

def simulate_test_alerts(test_mode, forecast_data):
    """Simulate alerts for testing using real forecast data"""
    test_alerts = []

    # Get real temperature data from forecast for realistic test
    current_temp = None
    min_temp_today = None

    if forecast_data:
        # Get current and min temps from real data
        temps_today = []
        for i, period in enumerate(forecast_data[:24]):  # Next 24 hours
            temp = period.get("temperature", None)
            if isinstance(temp, dict):
                temp = temp.get("value", None)
            if temp is not None and isinstance(temp, (int, float)):
                temps_today.append(temp)
                if i == 0:
                    current_temp = temp

        if temps_today:
            min_temp_today = min(temps_today)

    # Use real temps if available, otherwise use defaults
    if current_temp is None:
        current_temp = 45
    if min_temp_today is None:
        min_temp_today = 38

    if test_mode in ["frost1", "all"]:
        test_alerts.append({
            "type": "TEST FIRST FROST",
            "message": f"TEST ALERT - First frost\nCurrent: {current_temp:.0f}F\nTonight low: {min_temp_today:.0f}F\nSimulated frost: 28F\nDuration: 3hrs",
            "event": {"start_time": "TEST", "duration_hours": 3, "min_temp": 28}
        })

    if test_mode in ["frost2", "all"]:
        test_alerts.append({
            "type": "TEST SECOND FROST",
            "message": f"TEST ALERT - Second frost\nCurrent: {current_temp:.0f}F\nTonight low: {min_temp_today:.0f}F\nSimulated frost: 30F\nDuration: 2hrs",
            "event": {"start_time": "TEST", "duration_hours": 2, "min_temp": 30}
        })

    if test_mode in ["extended_freeze", "all"]:
        test_alerts.append({
            "type": "TEST EXTENDED FREEZE",
            "message": f"TEST ALERT - Extended freeze\nCurrent: {current_temp:.0f}F\nTonight low: {min_temp_today:.0f}F\nSimulated freeze: 25F\nDuration: 6hrs",
            "event": {"start_time": "TEST", "duration_hours": 6, "min_temp": 25}
        })

    return test_alerts

def main():
    """Main function to check weather and send alerts"""
    import sys

    # Check for test mode
    test_mode = None
    force_run = False
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if arg.lower() == "--force":
                force_run = True
            else:
                test_mode = arg.lower()

        if test_mode in ["frost1", "frost2", "extended_freeze", "all"]:
            print(f"Running in TEST MODE: {test_mode}")
            print(f"Fetching real weather data for {LOCATION_NAME}...")

            # Get real forecast data for the test
            forecast = get_weather_forecast()

            alerts = simulate_test_alerts(test_mode, forecast)
            if alerts:
                print(f"Sending {len(alerts)} TEST alert(s) with real weather data")
                send_email_alert(alerts)
            return
        elif test_mode is not None and test_mode != "--force":
            print(f"Invalid test mode: {test_mode}")
            print("Valid options: frost1, frost2, extended_freeze, all")
            return

    print(f"Checking weather for {LOCATION_NAME}...")

    if not force_run and os.environ.get("FORCE_RUN") != "1":
        pacific_now = get_pacific_now()
        if pacific_now.hour not in SCHEDULED_HOURS:
            print(
                "Current Pacific time is "
                f"{pacific_now.strftime('%m/%d %I:%M%p %Z')} - outside the scheduled alert window."
            )
            print("Skipping weather check until the next 6 AM/PM Pacific run.")
            return

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
