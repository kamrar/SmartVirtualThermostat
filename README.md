# SmartVirtualThermostat
Smart Virtual Thermostat python plugin for Domoticz home automation system

See https://www.domoticz.com/wiki/Plugins/Smart_Virtual_Thermostat.html for description and installation instructions

## New Features (January 2026)

### Smart Hysteresis & Active Monitoring
This version introduces active temperature monitoring to solve the "undershoot/overshoot" dilemma common with gas boiler systems.

*   **Active Monitoring:** Instead of blindly running for the calculated duration, the thermostat now checks the temperature every minute while heating.
*   **Smart Hysteresis:** Heating stops exactly when the target temperature is reached (minus a small inertia buffer), provided the boiler has run for its minimum efficient time.
*   **Safety Override:** If the temperature exceeds the setpoint by 0.3°C, heating is cut off immediately, ignoring minimum runtime constraints to prevent overheating.
*   **Enhanced Learning:** If heating stops early, the learning algorithm records the *actual* effective power used, ensuring future calculations are more accurate.
*   **Anti-Stall Logic:** Fixed a bug where the thermostat would get "stuck" at low power if the temperature didn't change during a cycle. It now aggressively ramps up power in these situations.
