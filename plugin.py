"""
Smart Virtual Thermostat python plugin for Domoticz
Author: Logread,
        adapted from the Vera plugin by Antor, see:
            http://www.antor.fr/apps/smart-virtual-thermostat-eng-2/?lang=en
            https://github.com/AntorFr/SmartVT
Version: 0.66.0 (September 28, 2025) - Major efficiency improvements for gas boiler systems
Enhanced Features:
- Smart overshoot prevention with configurable tolerance
- Minimum efficient runtime logic for gas boiler systems
- Boiler startup time compensation
- Temperature trend analysis for predictive heating
- Configurable heating system profiles (gas boiler, electric, heat pump, underfloor)
- Enhanced logging and efficiency metrics
- Improved minimum heating logic with Mode4 fixes
- Comprehensive efficiency optimizations for gas radiator systems
"""
"""
<plugin key="SVT" name="Smart Virtual Thermostat" author="logread" version="0.66.0" wikilink="https://www.domoticz.com/wiki/Plugins/Smart_Virtual_Thermostat.html" externallink="https://github.com/999LV/SmartVirtualThermostat.git">
    <description>
        <h2>Smart Virtual Thermostat</h2><br/>
        Easily implement in Domoticz an advanced virtual thermostat based on time modulation<br/>
        and self learning of relevant room thermal characteristics (including insulation level)<br/>
        rather then more conventional hysteresis methods, so as to achieve a greater comfort.<br/>
        It is a port to Domoticz of the original Vera plugin from Antor.<br/>
        <h3>Set-up and Configuration</h3>
        See domoticz wiki above.<br/> 
    </description>
    <params>
        <param field="Address" label="Domoticz IP Address" width="200px" required="true" default="localhost"/>
        <param field="Port" label="Port" width="40px" required="true" default="8080"/>
        <param field="Username" label="Username" width="200px" required="false" default=""/>
        <param field="Password" label="Password" width="200px" required="false" default=""/>
        <param field="Mode1" label="Inside Temperature Sensors (csv list of idx)" width="100px" required="true" default="0"/>
        <param field="Mode2" label="Outside Temperature Sensors (csv list of idx)" width="100px" required="false" default=""/>
        <param field="Mode3" label="Heating Switches (csv list of idx)" width="100px" required="true" default="0"/>
        <param field="Mode4" label="Apply minimum heating per cycle" width="200px">
            <options>
                <option label="only when heating required" value="Normal" default="true" />
                <option label="always" value="Forced"/>
            </options>
        </param>
        <param field="Mode5" label="Calc. cycle, Min. Heating time /cycle, Pause On delay, Pause Off delay, Forced mode duration (all in minutes), Delta max (°C)" width="200px" required="true" default="30,0,2,1,60,0.2"/>
        <param field="Mode7" label="Heating System Profile" width="200px">
            <options>
                <option label="Gas Boiler + Radiators" value="gas_radiator" default="true"/>
                <option label="Electric Heating" value="electric"/>
                <option label="Heat Pump" value="heat_pump"/>
                <option label="Underfloor Heating" value="underfloor"/>
                <option label="Custom" value="custom"/>
            </options>
        </param>
        <param field="Mode8" label="Boiler Startup Time, Min Efficient Runtime, Radiator Lag Time (minutes), Hysteresis Offset (0.0-2.0)" width="200px" required="false" default="3,10,12,0.0"/>
        <param field="Mode6" label="Logging Level" width="200px">
            <options>
                <option label="Normal" value="Normal"  default="true"/>
                <option label="Verbose" value="Verbose"/>
                <option label="Debug - Python Only" value="2"/>
                <option label="Debug - Basic" value="62"/>
                <option label="Debug - Basic+Messages" value="126"/>
                <option label="Debug - Connections Only" value="16"/>
                <option label="Debug - Connections+Queue" value="144"/>
                <option label="Debug - All" value="-1"/>
            </options>
        </param>
    </params>
</plugin>
"""
import Domoticz
import json
from urllib import parse, request
from datetime import datetime, timedelta
import time
import base64
import itertools
from distutils.version import LooseVersion

class deviceparam:

    def __init__(self, unit, nvalue, svalue):
        self.unit = unit
        self.nvalue = nvalue
        self.svalue = svalue


class BasePlugin:

    def __init__(self):

        self.debug = False
        self.calculate_period = 30  # Time in minutes between two calculations (cycle)
        self.minheatpower = 0  # if heating is needed, minimum heat power (in % of calculation period)
        self.deltamax = 0.2  # allowed temp excess over setpoint temperature
        self.pauseondelay = 2  # time between pause sensor actuation and actual pause
        self.pauseoffdelay = 1  # time between end of pause sensor actuation and end of actual pause
        self.forcedduration = 60  # time in minutes for the forced mode
        self.ActiveSensors = {}
        self.InTempSensors = []
        self.OutTempSensors = []
        self.Heaters = []
        # Heating System Profiles
        self.HeatingProfiles = {
            'gas_radiator': {
                'startup_time': 3,
                'min_runtime': 10,
                'lag_time': 12,
                'overshoot_tolerance': 0.1,
                'efficiency_priority': True
            },
            'electric': {
                'startup_time': 0,
                'min_runtime': 2,
                'lag_time': 5,
                'overshoot_tolerance': 0.05,
                'efficiency_priority': False
            },
            'heat_pump': {
                'startup_time': 2,
                'min_runtime': 15,
                'lag_time': 8,
                'overshoot_tolerance': 0.1,
                'efficiency_priority': True
            },
            'underfloor': {
                'startup_time': 5,
                'min_runtime': 20,
                'lag_time': 30,
                'overshoot_tolerance': 0.2,
                'efficiency_priority': True
            },
            'custom': {
                'startup_time': 3,
                'min_runtime': 10,
                'lag_time': 12,
                'overshoot_tolerance': 0.1,
                'efficiency_priority': True
            }
        }
        
        self.InternalsDefaults = {
            'ConstC': float(60),  # inside heating coeff, depends on room size & power of your heater (60 by default)
            'ConstT': float(1),  # external heating coeff,depends on the insulation relative to the outside (1 by default)
            'nbCC': 0,  # number of learnings for ConstC
            'nbCT': 0,  # number of learnings for ConstT
            'LastPwr': 0,  # % power from last calculation
            'LastInT': float(0),  # inside temperature at last calculation
            'LastOutT': float(0),  # outside temprature at last calculation
            'LastSetPoint': float(20),  # setpoint at time of last calculation
            'ALStatus': 0,  # AutoLearning status (0 = uninitialized, 1 = initialized, 2 = disabled)
            # Enhanced variables for smart heating
            'HeatingSystemType': 'gas_radiator',
            'BoilerStartupTime': 3,
            'MinEfficiencyRuntime': 10,
            'RadiatorLagTime': 12,
            'OvershootTolerance': 0.1,
            'HysteresisOffset': 0.0,
            'LastThreeTemps': [20.0, 20.0, 20.0],
            'EfficiencyMode': True,
            'TotalHeatingTime': 0,
            'EffectiveHeatingTime': 0,
            'HeatingCycles': 0,
            'OvershootEvents': 0,
            'InefficientCycles': 0}
        self.Internals = self.InternalsDefaults.copy()
        self.heat = False
        self.pause = False
        self.pauserequested = False
        self.pauserequestchangedtime = datetime.now()
        self.forced = False
        self.intemp = 20.0
        self.outtemp = 20.0
        self.setpoint = 20.0
        self.endheat = datetime.now()
        self.nextcalc = self.endheat
        self.lastcalc = self.endheat
        self.nextupdate = self.endheat
        self.nexttemps = self.endheat
        self.learn = True
        self.loglevel = None
        self.statussupported = True
        self.intemperror = False
        return


    def onStart(self):
        # setup the appropriate logging level
        try:
            debuglevel = int(Parameters["Mode6"])
        except ValueError:
            debuglevel = 0
            self.loglevel = Parameters["Mode6"]
        if debuglevel != 0:
            self.debug = True
            Domoticz.Debugging(debuglevel)
            DumpConfigToLog()
            self.loglevel = "Verbose"
        else:
            self.debug = False
            Domoticz.Debugging(0)

        # check if the host domoticz version supports the Domoticz.Status() python framework function
        try:
            Domoticz.Status("This version of domoticz allows status logging by the plugin (in verbose mode)")
        except Exception:
            self.statussupported = False

        # create the child devices if these do not exist yet
        devicecreated = []
        if 1 not in Devices:
            Options = {"LevelActions": "||",
                       "LevelNames": "Off|Auto|Forced",
                       "LevelOffHidden": "false",
                       "SelectorStyle": "0"}
            Domoticz.Device(Name="Thermostat Control", Unit=1, TypeName="Selector Switch", Switchtype=18, Image=15,
                            Options=Options, Used=1).Create()
            devicecreated.append(deviceparam(1, 0, "0"))  # default is Off state
        if 2 not in Devices:
            Options = {"LevelActions": "||",
                       "LevelNames": "Off|Normal|Economy",
                       "LevelOffHidden": "true",
                       "SelectorStyle": "0"}
            Domoticz.Device(Name="Thermostat Mode", Unit=2, TypeName="Selector Switch", Switchtype=18, Image=15,
                            Options=Options, Used=1).Create()
            devicecreated.append(deviceparam(2, 0, "10"))  # default is normal mode
        if 3 not in Devices:
            Domoticz.Device(Name="Thermostat Pause", Unit=3, TypeName="Switch", Image=9, Used=1).Create()
            devicecreated.append(deviceparam(3, 0, ""))  # default is Off
        if 4 not in Devices:
            Domoticz.Device(Name="Setpoint Normal", Unit=4, Type=242, Subtype=1, Used=1).Create()
            devicecreated.append(deviceparam(4, 0, "20"))  # default is 20 degrees
        if 5 not in Devices:
            Domoticz.Device(Name="Setpoint Economy", Unit=5, Type=242, Subtype=1, Used=1).Create()
            devicecreated.append(deviceparam(5 ,0, "17"))  # default is 17 degrees
        if 6 not in Devices:
            Domoticz.Device(Name="Thermostat temp", Unit=6, TypeName="Temperature").Create()
            devicecreated.append(deviceparam(6, 0, "20"))  # default is 20 degrees

        # if any device has been created in onStart(), now is time to update its defaults
        for device in devicecreated:
            Devices[device.unit].Update(nValue=device.nvalue, sValue=device.svalue)

        # build lists of sensors and switches
        self.InTempSensors = parseCSV(Parameters["Mode1"])
        self.WriteLog("Inside Temperature sensors = {}".format(self.InTempSensors), "Verbose")
        self.OutTempSensors = parseCSV(Parameters["Mode2"])
        self.WriteLog("Outside Temperature sensors = {}".format(self.OutTempSensors), "Verbose")
        self.Heaters = parseCSV(Parameters["Mode3"])
        self.WriteLog("Heaters = {}".format(self.Heaters), "Verbose")
        
        # build dict of status of all temp sensors to be used when handling timeouts
        for sensor in itertools.chain(self.InTempSensors, self.OutTempSensors):
            self.ActiveSensors[sensor] = True

        # splits additional parameters
        params = parseCSV(Parameters["Mode5"])
        if len(params) == 5 or len(params) == 6:
            self.calculate_period = CheckParam("Calculation Period", params[0], 30)
            if self.calculate_period < 5:
                Domoticz.Error("Invalid calculation period parameter. Using minimum of 5 minutes !")
                self.calculate_period = 5
            self.minheatpower = CheckParam("Minimum Heating (%)", params[1], 0)
            if self.minheatpower > 100:
                Domoticz.Error("Invalid minimum heating parameter. Using maximum of 100% !")
                self.minheatpower = 100
            self.pauseondelay = CheckParam("Pause On Delay", params[2], 2)
            self.pauseoffdelay = CheckParam("Pause Off Delay", params[3], 0)
            self.forcedduration = CheckParam("Forced Mode Duration", params[4], 60)
            if self.forcedduration < 15:
                Domoticz.Error("Invalid forced mode duration parameter. Using minimum of 15 minutes !")
                self.forcedduration = 15
            if len(params) > 5:
                self.deltamax = CheckParam("Delta max", params[5], 0.2)
            else:
                Domoticz.Error("Delta max missing in parameters. Add the field in the plugin configuration (default value=0.2)")
        else:
            Domoticz.Error("Error reading Mode5 parameters")

        # Parse heating system profile (Mode7)
        heating_profile = Parameters.get("Mode7", "gas_radiator")
        if heating_profile in self.HeatingProfiles:
            profile = self.HeatingProfiles[heating_profile]
            self.Internals['HeatingSystemType'] = heating_profile
            self.Internals['OvershootTolerance'] = profile['overshoot_tolerance']
            self.WriteLog("Using heating system profile: {}".format(heating_profile), "Verbose")
        else:
            self.WriteLog("Unknown heating system profile: {}, using gas_radiator".format(heating_profile), "Normal")
            heating_profile = "gas_radiator"
            profile = self.HeatingProfiles[heating_profile]

        # Parse custom heating system parameters (Mode8)
        self.Internals['HysteresisOffset'] = 0.0 # Default value
        
        if Parameters.get("Mode8", ""):
            heating_params = parseCSV(Parameters["Mode8"])
            if len(heating_params) >= 3:
                self.Internals['BoilerStartupTime'] = CheckParam("Boiler Startup Time", heating_params[0], profile['startup_time'])
                self.Internals['MinEfficiencyRuntime'] = CheckParam("Min Efficiency Runtime", heating_params[1], profile['min_runtime'])
                self.Internals['RadiatorLagTime'] = CheckParam("Radiator Lag Time", heating_params[2], profile['lag_time'])
                
                if len(heating_params) >= 4:
                    self.Internals['HysteresisOffset'] = CheckParam("Hysteresis Offset", heating_params[3], 0.0)
                
                self.WriteLog("Custom heating parameters: Startup={}min, MinRuntime={}min, Lag={}min, Offset={}C".format(
                    self.Internals['BoilerStartupTime'], self.Internals['MinEfficiencyRuntime'], self.Internals['RadiatorLagTime'], self.Internals['HysteresisOffset']), "Verbose")
            else:
                # Use profile defaults
                self.Internals['BoilerStartupTime'] = profile['startup_time']
                self.Internals['MinEfficiencyRuntime'] = profile['min_runtime']
                self.Internals['RadiatorLagTime'] = profile['lag_time']
                self.WriteLog("Using profile defaults for heating system parameters", "Verbose")
        else:
            # Use profile defaults
            self.Internals['BoilerStartupTime'] = profile['startup_time']
            self.Internals['MinEfficiencyRuntime'] = profile['min_runtime']
            self.Internals['RadiatorLagTime'] = profile['lag_time']
            self.WriteLog("Using profile defaults for heating system parameters", "Verbose")

        # Validate heating parameters
        if self.Internals['MinEfficiencyRuntime'] < 5:
            self.WriteLog("Warning: MinEfficiencyRuntime below 5 minutes may be inefficient for most systems", "Normal")
        if self.Internals['BoilerStartupTime'] >= self.Internals['MinEfficiencyRuntime']:
            self.WriteLog("Warning: BoilerStartupTime should be less than MinEfficiencyRuntime", "Normal")

        # loads persistent variables from dedicated user variable
        # note: to reset the thermostat to default values (i.e. ignore all past learning),
        # just delete the relevant "<plugin name>-InternalVariables" user variable Domoticz GUI and restart plugin
        self.getUserVar()

        # if mode = off then make sure actual heating is off just in case if was manually set to on
        if Devices[1].sValue == "0":
            self.switchHeat(False)


    def onStop(self):
        Domoticz.Debugging(0)


    def onCommand(self, Unit, Command, Level, Color):

        Domoticz.Debug("onCommand called for Unit {}: Command '{}', Level: {}".format(Unit, Command, Level))

        if Unit == 3:  # pause switch
            self.pauserequestchangedtime = datetime.now()
            svalue = ""
            if str(Command) == "On":
                nvalue = 1
                self.pauserequested = True
            else:
                nvalue = 0
                self.pauserequested = False
        else:
            nvalue = 1 if Level > 0 else 0
            svalue = str(Level)

        Devices[Unit].Update(nValue=nvalue, sValue=svalue)

        if Unit in (1, 2, 4, 5): # force recalculation if control or mode or a setpoint changed
            self.nextcalc = datetime.now()
            self.learn = False
            self.onHeartbeat()


    def onHeartbeat(self):

        now = datetime.now()
        
        # fool proof checking.... based on users feedback
        if not all(device in Devices for device in (1,2,3,4,5,6)):
            Domoticz.Error("one or more devices required by the plugin is/are missing, please check domoticz device creation settings and restart !")
            return

        if Devices[1].sValue == "0":  # Thermostat is off
            if self.forced or self.heat:  # thermostat setting was just changed so we kill the heating
                self.forced = False
                self.endheat = now
                Domoticz.Debug("Switching heat Off !")
                self.switchHeat(False)

        elif Devices[1].sValue == "20":  # Thermostat is in forced mode
            if self.forced:
                if self.endheat <= now:
                    self.forced = False
                    self.endheat = now
                    Domoticz.Debug("Forced mode Off !")
                    Devices[1].Update(nValue=1, sValue="10")  # set thermostat to normal mode
                    self.switchHeat(False)
            else:
                self.forced = True
                self.endheat = now + timedelta(minutes=self.forcedduration)
                Domoticz.Debug("Forced mode On !")
                self.switchHeat(True)

        else:  # Thermostat is in mode auto

            if self.forced:  # thermostat setting was just changed from "forced" so we kill the forced mode
                self.forced = False
                self.endheat = now
                self.nextcalc = now   # this will force a recalculation on next heartbeat
                Domoticz.Debug("Forced mode Off !")
                self.switchHeat(False)

            elif (self.endheat <= now or self.pause) and self.heat:  # heat cycle is over
                self.endheat = now
                self.heat = False
                if self.Internals['LastPwr'] < 100:
                    self.switchHeat(False)
                # if power was 100(i.e. a full cycle), then we let the next calculation (at next heartbeat) decide
                # to switch off in order to avoid potentially damaging quick off/on cycles to the heater(s)

            elif self.pause and not self.pauserequested:  # we are in pause and the pause switch is now off
                if self.pauserequestchangedtime + timedelta(minutes=self.pauseoffdelay) <= now:
                    self.WriteLog("Pause is now Off", "Status")
                    self.pause = False

            elif not self.pause and self.pauserequested:  # we are not in pause and the pause switch is now on
                if self.pauserequestchangedtime + timedelta(minutes=self.pauseondelay) <= now:
                    self.WriteLog("Pause is now On", "Status")
                    self.pause = True
                    self.switchHeat(False)

            elif (self.nextcalc <= now) and not self.pause:  # we start a new calculation
                self.nextcalc = now + timedelta(minutes=self.calculate_period)
                self.WriteLog("Next calculation time will be : " + str(self.nextcalc), "Verbose")

                # make current setpoint used in calculation reflect the select mode (10= normal, 20 = economy)
                if Devices[2].sValue == "10":
                    self.setpoint = float(Devices[4].sValue)
                else:
                    self.setpoint = float(Devices[5].sValue)

                # call the Domoticz json API for a temperature devices update, to get the lastest temps...
                if self.readTemps():
                    # do the thermostat work
                    self.AutoMode()
                else:
                    # make sure we switch off heating if there was an error with reading the temp
                    self.switchHeat(False)

        if self.nexttemps <= now:
            # call the Domoticz json API for a temperature devices update, to get the lastest temps (and avoid the
            # connection time out time after 10mins that floods domoticz logs in versions of domoticz since spring 2018)
            self.readTemps()
            
            # Active Monitoring check
            if self.heat and not self.pause and not self.forced:
                self.checkTargetReached()

        # check if need to refresh setpoints so that they do not turn red in GUI
        if self.nextupdate <= now:
            self.nextupdate = now + timedelta(minutes=int(Settings["SensorTimeout"]))
            Devices[4].Update(nValue=0, sValue=Devices[4].sValue)
            Devices[5].Update(nValue=0, sValue=Devices[5].sValue)


    def checkTargetReached(self):
        """Active monitoring to stop heating when target is reached (Smart Hysteresis)"""
        if not self.heat:
            return

        # Smart Hysteresis parameters
        # InertiaOffset: Cut off slightly before setpoint to allow radiator heat to finish the job.
        # For gas radiators, 0.1C is a safe starting point.
        inertia_offset = self.Internals.get('HysteresisOffset', 0.0)
        safety_margin = 0.3 # If we exceed setpoint by this much, cut off regardless of minimum runtime
        
        target_temp = self.setpoint - inertia_offset
        
        if self.intemp >= target_temp:
            # Calculate how long we've been running
            # self.endheat is the scheduled end time.
            # Duration = self.Internals['LastPwr'] * self.calculate_period / 100
            # StartTime = EndTime - Duration
            # But simpler: we know when we calculated. self.lastcalc.
            # No, lastcalc is set at end of AutoMode.
            
            # We can approximate start time or just trust the logic:
            # We need to know if we satisfied the Minimum Efficient Runtime.
            
            run_time_min = (datetime.now() - self.lastcalc).total_seconds() / 60
            min_runtime = self.Internals.get('MinEfficiencyRuntime', 10)
            
            # Decision Logic
            stop_heating = False
            reason = ""
            
            if self.intemp >= self.setpoint + safety_margin:
                stop_heating = True
                reason = "Critical Overshoot Protection (>{})".format(safety_margin)
            elif run_time_min >= min_runtime:
                stop_heating = True
                reason = "Target Reached (Smart Hysteresis)"
            else:
                self.WriteLog("Target reached ({}) but keeping boiler on for minimum runtime ({:.1f}/{:.1f} min)".format(
                    self.intemp, run_time_min, min_runtime), "Verbose")
                return

            if stop_heating:
                self.WriteLog("Stopping heating early: {} | Temp: {:.1f}".format(reason, self.intemp), "Status")
                
                # Calculate effective power for learning
                # If we scheduled 20 mins but stopped at 10, LastPwr should be updated
                # so the system learns 10 mins was enough (or that the rate was higher).
                
                # Original Power %
                original_pwr = self.Internals.get('LastPwr', 0)
                
                # Effective Power %
                # effective_pwr = (actual_run_time / cycle_period) * 100
                effective_pwr = (run_time_min / self.calculate_period) * 100
                
                # Update Internals
                self.Internals['LastPwr'] = effective_pwr
                self.Internals['TotalHeatingTime'] = self.Internals.get('TotalHeatingTime', 0) - (original_pwr/100 * self.calculate_period) + run_time_min
                
                self.switchHeat(False)
                self.endheat = datetime.now() # Officially end the heating cycle
                self.heat = False
                self.saveUserVar()

    def AutoMode(self):
        """Enhanced smart heating algorithm with efficiency optimizations"""
        self.WriteLog("Temperatures: Inside = {} / Outside = {}".format(self.intemp, self.outtemp), "Verbose")
        
        # Update temperature history for trend analysis
        self.updateTempHistory()
        
        # Analyze temperature trends
        temp_trend = self.analyzeTempTrend()
        
        # Get current heating system configuration
        overshoot_tolerance = self.Internals.get('OvershootTolerance', 0.1)
        
        # Step 1: Check for overshoot with smart tolerance
        if self.intemp > self.setpoint + overshoot_tolerance:
            self.WriteLog("Temperature exceeds setpoint + tolerance ({:.1f}°C), no heating".format(overshoot_tolerance), "Status")
            power = 0
            overshoot = True
            reason = "Overshoot Prevention"
        
        # Step 2: Check if temperature is very close to setpoint and stable/rising
        elif abs(self.intemp - self.setpoint) <= 0.1 and (temp_trend['stable'] or temp_trend['rising']):
            self.WriteLog("Temperature near setpoint and stable/rising, no heating needed", "Verbose")
            power = 0
            overshoot = False
            reason = "Temperature Stable at Setpoint"
        
        else:
            overshoot = False
            # Step 3: Calculate heating power with learning
            if self.learn:
                self.AutoCallib()
            else:
                self.learn = True
                
            # Standard power calculation
            if self.outtemp is None:
                power = round((self.setpoint - self.intemp) * self.Internals["ConstC"], 1)
            else:
                power = round((self.setpoint - self.intemp) * self.Internals["ConstC"] +
                              (self.setpoint - self.outtemp) * self.Internals["ConstT"], 1)
            
            # Step 4: Apply predictive heating adjustments
            if temp_trend['falling'] and temp_trend['rate'] < -0.1:  # Significant temperature drop
                predictive_boost = min(10, abs(temp_trend['rate']) * 20)  # Up to 10% boost
                power += predictive_boost
                reason = "Predictive Heating (falling trend)"
                self.WriteLog("Applied predictive heating boost: +{:.1f}% due to falling trend".format(predictive_boost), "Verbose")
            else:
                reason = "Standard Calculation"

        # Step 5: Apply power limits
        if power < 0:
            power = 0  # lower limit
        elif power > 100:
            power = 100  # upper limit

        # Step 6: Apply smart minimum power logic
        power = self.applySmartMinimumPower(power, overshoot, reason)
        
        # Step 7: Apply efficiency runtime optimization
        power, final_duration = self.applyEfficiencyRuntime(power)
        
        # Step 8: Execute heating decision with enhanced logging
        self.executeSmartHeating(power, final_duration, reason, temp_trend)
        
        self.lastcalc = datetime.now()

    def updateTempHistory(self):
        """Update temperature history for trend analysis"""
        # Maintain last 3 temperatures for trend analysis
        self.Internals['LastThreeTemps'].append(self.intemp)
        if len(self.Internals['LastThreeTemps']) > 3:
            self.Internals['LastThreeTemps'].pop(0)

    def analyzeTempTrend(self):
        """Analyze temperature trends for predictive heating"""
        if len(self.Internals['LastThreeTemps']) >= 3:
            temps = self.Internals['LastThreeTemps']
            # Calculate trend over 2 cycles
            trend_rate = (temps[-1] - temps[0]) / 2  # °C per cycle
            
            trend_info = {
                'falling': trend_rate < -0.05,
                'rising': trend_rate > 0.05,
                'stable': abs(trend_rate) <= 0.05,
                'rate': trend_rate,
                'significant': abs(trend_rate) > 0.1
            }
            
            self.WriteLog("Temperature trend: rate={:.3f}°C/cycle, {}".format(
                trend_rate, 'falling' if trend_info['falling'] else 'rising' if trend_info['rising'] else 'stable'), "Verbose")
            
            return trend_info
        
        return {'falling': False, 'rising': False, 'stable': True, 'rate': 0, 'significant': False}

    def applySmartMinimumPower(self, calculated_power, overshoot, reason):
        """Apply intelligent minimum power logic"""
        if calculated_power <= 0:
            return 0
            
        # Don't apply minimum power during overshoot
        if overshoot:
            return 0
            
        # Apply minimum power based on Mode4 setting and efficiency considerations
        mode4_setting = Parameters.get("Mode4", "Normal")
        
        if calculated_power <= self.minheatpower:
            if mode4_setting == "Forced":
                self.WriteLog("Calculated power is {:.1f}%, applying minimum power of {}% (forced mode)".format(
                    calculated_power, self.minheatpower), "Verbose")
                return self.minheatpower
            elif calculated_power > 0:  # Only apply minimum if heating is actually needed
                self.WriteLog("Calculated power is {:.1f}%, applying minimum power of {}% (heating required)".format(
                    calculated_power, self.minheatpower), "Verbose")
                return self.minheatpower
        
        return calculated_power

    def applyEfficiencyRuntime(self, power_percent):
        """Apply minimum efficient runtime for boiler systems"""
        if power_percent <= 0:
            return 0, 0
        
        # Calculate initial duration from power percentage
        initial_duration = round(power_percent * self.calculate_period / 100)
        
        # Get efficiency parameters
        startup_time = self.Internals.get('BoilerStartupTime', 3)
        min_runtime = self.Internals.get('MinEfficiencyRuntime', 10)
        efficiency_mode = self.Internals.get('EfficiencyMode', True)
        
        if not efficiency_mode:
            return power_percent, initial_duration
        
        # Calculate minimum total time (startup + effective heating)
        min_total_time = startup_time + min_runtime
        
        if initial_duration > 0 and initial_duration < min_total_time:
            # Adjust power to meet minimum efficient runtime
            efficient_duration = min_total_time
            efficient_power = min(efficient_duration * 100 / self.calculate_period, 100)
            
            self.WriteLog("Efficiency optimization: extending from {:.1f}min to {:.1f}min (power: {:.1f}% -> {:.1f}%)".format(
                initial_duration, efficient_duration, power_percent, efficient_power), "Status")
            
            # Track efficiency metrics
            self.Internals['HeatingCycles'] = self.Internals.get('HeatingCycles', 0) + 1
            
            return efficient_power, efficient_duration
        
        return power_percent, initial_duration

    def executeSmartHeating(self, power, duration, reason, temp_trend):
        """Execute heating decision with comprehensive logging"""
        
        # Enhanced logging
        self.WriteLog("=== HEATING DECISION ===", "Status")
        self.WriteLog("Power: {:.1f}% | Duration: {:.1f}min | Reason: {}".format(power, duration, reason), "Status")
        self.WriteLog("Current: {:.1f}°C | Setpoint: {:.1f}°C | Trend: {:.3f}°C/cycle".format(
            self.intemp, self.setpoint, temp_trend.get('rate', 0)), "Status")
        
        if power == 0:
            self.switchHeat(False)
            self.WriteLog("No heating requested", "Verbose")
        else:
            # Calculate end time and switch on
            self.endheat = datetime.now() + timedelta(minutes=duration)
            self.WriteLog("Heating until: {}".format(self.endheat.strftime("%H:%M:%S")), "Status")
            
            # Update efficiency metrics
            self.Internals['TotalHeatingTime'] = self.Internals.get('TotalHeatingTime', 0) + duration
            startup_time = self.Internals.get('BoilerStartupTime', 3)
            effective_time = max(0, duration - startup_time)
            self.Internals['EffectiveHeatingTime'] = self.Internals.get('EffectiveHeatingTime', 0) + effective_time
            
            # Switch heating on
            self.switchHeat(True)
            
            # Ensure next active monitor check is soon (1 min)
            self.nexttemps = datetime.now() + timedelta(minutes=1)
            
            # Update learning variables
            if self.Internals["ALStatus"] < 2:
                self.Internals['LastPwr'] = power
                self.Internals['LastInT'] = self.intemp
                self.Internals['LastOutT'] = self.outtemp
                self.Internals['LastSetPoint'] = self.setpoint
                self.Internals['ALStatus'] = 1
                self.saveUserVar()
        
        # Log efficiency metrics periodically
        if self.Internals.get('HeatingCycles', 0) % 10 == 0 and self.Internals.get('HeatingCycles', 0) > 0:
            self.logEfficiencyMetrics()

    def logEfficiencyMetrics(self):
        """Log efficiency metrics for monitoring"""
        total_time = self.Internals.get('TotalHeatingTime', 1)  # Avoid division by zero
        effective_time = self.Internals.get('EffectiveHeatingTime', 0)
        cycles = self.Internals.get('HeatingCycles', 0)
        
        if total_time > 0:
            efficiency_ratio = effective_time / total_time * 100
            avg_cycle_length = total_time / max(cycles, 1)
            
            self.WriteLog("=== EFFICIENCY METRICS ===", "Status")
            self.WriteLog("Total Cycles: {} | Avg Duration: {:.1f}min | Efficiency: {:.1f}%".format(
                cycles, avg_cycle_length, efficiency_ratio), "Status")


    def AutoCallib(self):

        now = datetime.now()
        if self.Internals['ALStatus'] != 1:  # not initalized... do nothing
            Domoticz.Debug("Fist pass at AutoCallib... no callibration")
            pass
        elif self.Internals['LastPwr'] == 0:  # heater was off last time, do nothing
            Domoticz.Debug("Last power was zero... no callibration")
            pass
        elif self.Internals['LastPwr'] == 100 and self.intemp < self.Internals['LastSetPoint']:
            # heater was on max but setpoint was not reached... no learning
            Domoticz.Debug("Last power was 100% but setpoint not reached... no callibration")
            pass
        elif self.Internals['LastPwr'] > 0 and self.intemp <= self.Internals['LastInT'] and \
             self.Internals['LastSetPoint'] > self.Internals['LastInT']:
            # Heater was on but temp did not rise (or dropped), AND we were trying to raise the temperature.
            # This implies the current power was insufficient to overcome losses.
            # We must increase ConstC significantly to request more power next time.
            
            # We simulate a small temperature rise to avoid division by zero and force a higher ConstC.
            Domoticz.Debug("Heater was on ({:.1f}%) but temperature did not rise ({} -> {}). forcing ConstC increase.".format(
                self.Internals['LastPwr'], self.Internals['LastInT'], self.intemp))
            
            # Assume a virtual rise that is very small compared to the gap we wanted to bridge
            # This makes the ratio (Target-Start)/(Actual-Start) very large, increasing ConstC
            virtual_rise = 0.05
            
            ConstC = (self.Internals['ConstC'] * ((self.Internals['LastSetPoint'] - self.Internals['LastInT']) /
                                                  virtual_rise *
                                                  (timedelta.total_seconds(now - self.lastcalc) /
                                                   (self.calculate_period * 60))))
            
            # Ensure ConstC calculation result is positive (safety check)
            if ConstC < 0:
                 ConstC = self.Internals['ConstC'] * 1.5 # Fallback multiplier
            else:
                 # Cap the single-step increase to avoid extreme spikes, but ensure it's aggressive
                 ConstC = min(ConstC, self.Internals['ConstC'] * 2.0)
            
            self.WriteLog("Forced calc for ConstC = {} (due to lack of temp rise)".format(ConstC), "Verbose")
            self.Internals['ConstC'] = round((self.Internals['ConstC'] * self.Internals['nbCC'] + ConstC) /
                                             (self.Internals['nbCC'] + 1), 1)
            self.Internals['nbCC'] = min(self.Internals['nbCC'] + 1, 50)
            self.WriteLog("ConstC updated to {}".format(self.Internals['ConstC']), "Verbose")

        elif self.intemp > self.Internals['LastInT'] and self.Internals['LastSetPoint'] > self.Internals['LastInT']:
            # learning ConstC
            ConstC = (self.Internals['ConstC'] * ((self.Internals['LastSetPoint'] - self.Internals['LastInT']) /
                                                  (self.intemp - self.Internals['LastInT']) *
                                                  (timedelta.total_seconds(now - self.lastcalc) /
                                                   (self.calculate_period * 60))))
            self.WriteLog("New calc for ConstC = {}".format(ConstC), "Verbose")
            self.Internals['ConstC'] = round((self.Internals['ConstC'] * self.Internals['nbCC'] + ConstC) /
                                             (self.Internals['nbCC'] + 1), 1)
            self.Internals['nbCC'] = min(self.Internals['nbCC'] + 1, 50)
            self.WriteLog("ConstC updated to {}".format(self.Internals['ConstC']), "Verbose")
        elif (self.outtemp is not None and self.Internals['LastOutT'] is not None) and \
                 self.Internals['LastSetPoint'] > self.Internals['LastOutT']:
            # learning ConstT
            ConstT = (self.Internals['ConstT'] + ((self.Internals['LastSetPoint'] - self.intemp) /
                                                  (self.Internals['LastSetPoint'] - self.Internals['LastOutT']) *
                                                  self.Internals['ConstC'] *
                                                  (timedelta.total_seconds(now - self.lastcalc) /
                                                   (self.calculate_period * 60))))
            self.WriteLog("New calc for ConstT = {}".format(ConstT), "Verbose")
            self.Internals['ConstT'] = round((self.Internals['ConstT'] * self.Internals['nbCT'] + ConstT) /
                                             (self.Internals['nbCT'] + 1), 1)
            self.Internals['nbCT'] = min(self.Internals['nbCT'] + 1, 50)
            self.WriteLog("ConstT updated to {}".format(self.Internals['ConstT']), "Verbose")


    def switchHeat(self, switch):

        # Build list of heater switches, with their current status,
        # to be used to check if any of the heaters is already in desired state
        switches = {}
        devicesAPI = DomoticzAPI("type=devices&filter=light&used=true&order=Name")
        if devicesAPI:
            for device in devicesAPI["result"]:  # parse the switch device
                idx = int(device["idx"])
                if idx in self.Heaters:  # this switch is one of our heaters
                    if "Status" in device:
                        switches[idx] = True if device["Status"] == "On" else False
                        Domoticz.Debug("Heater switch {} currently is '{}'".format(idx, device["Status"]))
                    else:
                        Domoticz.Error("Device with idx={} does not seem to be a switch !".format(idx))

        # fool proof checking.... based on users feedback
        if len(switches) == 0:
            Domoticz.Error("none of the devices in the 'heaters' parameter is a switch... no action !")
            return

        # flip on / off as needed
        self.heat = switch
        command = "On" if switch else "Off"
        Domoticz.Debug("Heating '{}'".format(command))
        for idx in self.Heaters:
            if switches[idx] != switch:  # check if action needed
                DomoticzAPI("type=command&param=switchlight&idx={}&switchcmd={}".format(idx, command))
        if switch:
            Domoticz.Debug("End Heat time = " + str(self.endheat))


    def readTemps(self):

        # set update flag for next temp update
        # If heating is active, we check every minute to catch the target reach event.
        # Otherwise, standard 5 minute polling.
        if self.heat:
            self.nexttemps = datetime.now() + timedelta(minutes=1)
        else:
            self.nexttemps = datetime.now() + timedelta(minutes=5)

        # fetch all the devices from the API and scan for sensors
        noerror = True
        listintemps = []
        listouttemps = []
        devicesAPI = DomoticzAPI("type=devices&filter=temp&used=true&order=Name")
        if devicesAPI:
            for device in devicesAPI["result"]:  # parse the devices for temperature sensors
                idx = int(device["idx"])
                if idx in self.InTempSensors:
                    if "Temp" in device:
                        Domoticz.Debug("device: {}-{} = {}".format(device["idx"], device["Name"], device["Temp"]))
                        # check temp sensor is not timed out
                        if not self.SensorTimedOut(idx, device["Name"], device["LastUpdate"]):
                            listintemps.append(device["Temp"])
                    else:
                        Domoticz.Error("device: {}-{} is not a Temperature sensor".format(device["idx"], device["Name"]))
                elif idx in self.OutTempSensors:
                    if "Temp" in device:
                        Domoticz.Debug("device: {}-{} = {}".format(device["idx"], device["Name"], device["Temp"]))
                        # check temp sensor is not timed out
                        if not self.SensorTimedOut(idx, device["Name"], device["LastUpdate"]):
                            listouttemps.append(device["Temp"])
                    else:
                        Domoticz.Error("device: {}-{} is not a Temperature sensor".format(device["idx"], device["Name"]))

        # calculate the average inside temperature
        nbtemps = len(listintemps)
        if nbtemps > 0:
            self.intemp = round(sum(listintemps) / nbtemps, 1)
            # update the dummy device showing the current thermostat temp
            Devices[6].Update(nValue=0, sValue=str(self.intemp), TimedOut=False)
            if self.intemperror:  # there was previously an invalid inside temperature reading... reset to normal
                self.intemperror = False
                self.WriteLog("Inside Temperature reading is now valid again: Resuming normal operation", "Status")
                # we remove the timedout flag on the thermostat switch
                Devices[1].Update(nValue=Devices[1].nValue, sValue=Devices[1].sValue, TimedOut=False)
        else:
            # no valid inside temperature
            noerror = False
            if not self.intemperror:
                self.intemperror = True
                Domoticz.Error("No Inside Temperature found: Switching heating Off")
                self.switchHeat(False)
                # we mark both the thermostat switch and the thermostat temp devices as timedout
                Devices[1].Update(nValue=Devices[1].nValue, sValue=Devices[1].sValue, TimedOut=True)
                Devices[6].Update(nValue=Devices[6].nValue, sValue=Devices[6].sValue, TimedOut=True)

        # calculate the average outside temperature
        nbtemps = len(listouttemps)
        if nbtemps > 0:
            self.outtemp = round(sum(listouttemps) / nbtemps, 1)
        else:
            Domoticz.Debug("No Outside Temperature found...")
            self.outtemp = None

        Domoticz.Debug("Inside Temperature = {}".format(self.intemp))
        Domoticz.Debug("Outside Temperature = {}".format(self.outtemp))
        return noerror


    def getUserVar(self):

        variables = DomoticzAPI("type=command&param=getuservariables")
        if variables:
            # there is a valid response from the API but we do not know if our variable exists yet
            novar = True
            varname = Parameters["Name"] + "-InternalVariables"
            valuestring = ""
            if "result" in variables:
                for variable in variables["result"]:
                    if variable["Name"] == varname:
                        valuestring = variable["Value"]
                        novar = False
                        break
            if novar:
                # create user variable since it does not exist
                self.WriteLog("User Variable {} does not exist. Creation requested".format(varname), "Verbose")

                #check for Domoticz version:
                # there is a breaking change on dzvents_version 2.4.9, API was changed from 'saveuservariable' to 'adduservariable'
                # using 'saveuservariable' on latest versions returns a "status = 'ERR'" error

                # get a status of the actual running Domoticz instance, set the parameter accordigly
                parameter = "saveuservariable"
                domoticzInfo = DomoticzAPI("type=command&param=getversion")
                if domoticzInfo is None:
                    Domoticz.Error("Unable to fetch Domoticz info... unable to determine version")
                else:
                    if domoticzInfo and LooseVersion(domoticzInfo["dzvents_version"]) >= LooseVersion("2.4.9"):
                        self.WriteLog("Use 'adduservariable' instead of 'saveuservariable'", "Verbose")
                        parameter = "adduservariable"
                
                # actually calling Domoticz API
                DomoticzAPI("type=command&param={}&vname={}&vtype=2&vvalue={}".format(
                    parameter, varname, str(self.InternalsDefaults)))
                
                self.Internals = self.InternalsDefaults.copy()  # we re-initialize the internal variables
            else:
                try:
                    self.Internals.update(eval(valuestring))
                except:
                    self.Internals = self.InternalsDefaults.copy()
                return
        else:
            Domoticz.Error("Cannot read the uservariable holding the persistent variables")
            self.Internals = self.InternalsDefaults.copy()


    def saveUserVar(self):

        varname = Parameters["Name"] + "-InternalVariables"
        
        # Filter and compact Internals to avoid "String exceeds maximum size" error
        # We only need to save dynamic learning data, not static configuration.
        keys_to_save = [
            'ConstC', 'nbCC', 'ConstT', 'nbCT',
            'LastPwr', 'LastInT', 'LastOutT', 'LastSetPoint',
            'LastThreeTemps', 'ALStatus',
            'TotalHeatingTime', 'EffectiveHeatingTime', 'HeatingCycles',
            'InefficientCycles', 'OvershootEvents'
        ]
        
        compact_internals = {}
        for key in keys_to_save:
            if key in self.Internals:
                val = self.Internals[key]
                # Round floats in lists
                if isinstance(val, list):
                    val = [round(x, 1) if isinstance(x, float) else x for x in val]
                # Round individual floats
                elif isinstance(val, float):
                    val = round(val, 2)
                compact_internals[key] = val
            
        # Try update first
        result = DomoticzAPI("type=command&param=updateuservariable&vname={}&vtype=2&vvalue={}".format(
            varname, str(compact_internals)))
        
        # If update failed (e.g. variable doesn't exist), try add/save
        if result is None:
            Domoticz.Log("Update variable failed, trying to re-create variable {}".format(varname))
            # Determine correct call based on version (logic copied from getUserVar)
            parameter = "saveuservariable"
            domoticzInfo = DomoticzAPI("type=command&param=getversion")
            if domoticzInfo and LooseVersion(domoticzInfo["dzvents_version"]) >= LooseVersion("2.4.9"):
                parameter = "adduservariable"
            
            DomoticzAPI("type=command&param={}&vname={}&vtype=2&vvalue={}".format(
                parameter, varname, str(compact_internals)))


    def WriteLog(self, message, level="Normal"):

        if (self.loglevel == "Verbose" and level == "Verbose") or level == "Status":
            if self.statussupported:
                Domoticz.Status(message)
            else:
                Domoticz.Log(message)
        elif level == "Normal":
            Domoticz.Log(message)


    def SensorTimedOut(self, idx, name, datestring):

        def LastUpdate(datestring):
            dateformat = "%Y-%m-%d %H:%M:%S"
            # the below try/except is meant to address an intermittent python bug in some embedded systems
            try:
                result = datetime.strptime(datestring, dateformat)
            except TypeError:
                result = datetime(*(time.strptime(datestring, dateformat)[0:6]))
            return result

        timedout = LastUpdate(datestring) + timedelta(minutes=int(Settings["SensorTimeout"])) < datetime.now()

        # handle logging of time outs... only log when status changes (less clutter in logs)
        if timedout:
            if self.ActiveSensors[idx]:
                Domoticz.Error("skipping timed out temperature sensor '{}'".format(name))
                self.ActiveSensors[idx] = False
        else:
            if not self.ActiveSensors[idx]:
                self.WriteLog("previously timed out temperature sensor '{}' is back online".format(name), "Status")
                self.ActiveSensors[idx] = True

        return timedout


global _plugin
_plugin = BasePlugin()


def onStart():
    global _plugin
    _plugin.onStart()


def onStop():
    global _plugin
    _plugin.onStop()


def onCommand(Unit, Command, Level, Color):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Color)


def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()


# Plugin utility functions ---------------------------------------------------

def parseCSV(strCSV):

    listvals = []
    for value in strCSV.split(","):
        try:
            val = int(value)
        except ValueError:
            try:
                val = float(value)
            except ValueError:
                continue
        listvals.append(val)
    return listvals


def DomoticzAPI(APICall):

    resultJson = None
    url = "http://{}:{}/json.htm?{}".format(Parameters["Address"], Parameters["Port"], parse.quote(APICall, safe="&="))
    Domoticz.Debug("Calling domoticz API: {}".format(url))
    try:
        req = request.Request(url)
        if Parameters["Username"] != "":
            Domoticz.Debug("Add authentification for user {}".format(Parameters["Username"]))
            credentials = ('%s:%s' % (Parameters["Username"], Parameters["Password"]))
            encoded_credentials = base64.b64encode(credentials.encode('ascii'))
            req.add_header('Authorization', 'Basic %s' % encoded_credentials.decode("ascii"))

        response = request.urlopen(req)
        if response.status == 200:
            resultJson = json.loads(response.read().decode('utf-8'))
            if resultJson["status"] != "OK":
                Domoticz.Error("Domoticz API returned an error: status = {}. Msg: {}".format(
                    resultJson["status"], resultJson.get("message", "No message")))
                resultJson = None
        else:
            Domoticz.Error("Domoticz API: http error = {}".format(response.status))
    except:
        Domoticz.Error("Error calling '{}'".format(url))
    return resultJson


def CheckParam(name, value, default):
    if isinstance(default, int) and isinstance(value, int):
        param = value
    elif isinstance(default, float) and (isinstance(value, float) or isinstance(value, int)):
        param = float(value)
    else:
        param = default
        Domoticz.Error("Parameter '{}' has an invalid value of '{}' ! defaut of '{}' is instead used.".format(name, value, default))
    return param


# Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug("'" + x + "':'" + str(Parameters[x]) + "'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))
    return
