# Alexa OAuth2 Integration for Home Assistant

**Status**: Phase 3 Complete - Full Device Entity Support
**Version**: 0.3.0
**Latest Update**: 2025-11-13

This integration provides OAuth2-authenticated control of Amazon Alexa smart home devices in Home Assistant. It discovers all your Alexa-connected devices and creates Home Assistant entities for switches, lights, thermostats, and sensors with full state synchronization and control capabilities.

---

## What's Implemented (Phases 1-3)

### Phase 1: OAuth2 Authentication ✅
- **RFC 7636 PKCE** - Secure OAuth2 implementation with proof key for code exchange
- **Automatic Token Refresh** - Home Assistant framework manages token lifecycle
- **Multi-Account Support** - Authenticate multiple Amazon accounts
- **Encrypted Token Storage** - No plain-text credentials
- **Automatic Reauth** - Triggers reauth flow on token expiry
- **100% Type Safe** - Full type hints throughout codebase

### Phase 2: Device Discovery ✅
- **Alexa Smart Home API Integration** - Queries Alexa for all connected devices
- **AlexaDeviceCoordinator** - Manages periodic device polling with Home Assistant's DataUpdateCoordinator
- **Two-Tiered Polling**:
  - Full device discovery every 15 minutes (detects new/removed devices)
  - State updates every 5 minutes (power, brightness, temperature, etc.)
- **Rate Limiting** - Token bucket with 20 burst capacity, 10 req/sec sustained
- **Circuit Breaker** - Prevents cascading failures after 5 consecutive errors
- **Exponential Backoff Retry** - Automatic retry with 1s, 2s, 4s delays + jitter
- **Comprehensive Error Handling** - Graceful degradation for network/API errors

### Phase 3: Device Entities ✅

#### Switch Platform (PowerController)
- **On/Off Control** - Toggle switches and smart plugs
- **State Feedback** - Real-time state from Alexa API
- **Device Registry Integration** - Groups related entities in Home Assistant
- **Supported Devices**: Smart plugs, switches, outlets

#### Light Platform (Brightness/Color Control)
- **Brightness Control** - 0-254 range (0-100% equivalent)
- **RGB Color Control** - Full HSV color selection (hue, saturation, brightness)
- **Color Temperature** - Warm to cool white (153-500 mireds)
- **Automatic Color Mode Detection** - Adapts UI based on device capabilities
- **Supported Devices**: Dimmable lights, RGB lights, color temperature lights, Philips Hue, LIFX, Nanoleaf, etc.

#### Climate Platform (Thermostat Control)
- **Temperature Monitoring** - Current and target temperature display
- **Target Temperature Control** - 10-38°C range with automatic clamping
- **HVAC Mode Selection** - OFF, HEAT, COOL, AUTO modes
- **HVAC Action Reporting** - IDLE, HEATING, COOLING status
- **Preset Modes** - Comfort, ECO, Away modes
- **Supported Devices**: Ecobee, Nest, Honeywell, and other HVAC systems

#### Sensor Platform (Read-Only State)
- **Temperature Sensors** - Current temperature with °C/°F display
- **Humidity Sensors** - Current humidity percentage
- **Contact Sensors** - Door and window open/close status
- **Motion Sensors** - Motion detection on/off
- **Battery Sensors** - Wireless device battery levels
- **Supported Devices**: Eve Room, Eve Outdoor, motion sensors, contact sensors, etc.

---

## What's Not Yet Implemented (Phase 4 - Planned)

- ⏳ **Automation Triggers** - Trigger automations on device state changes
- ⏳ **Webhook Support** - Real-time device events via webhooks
- ⏳ **Custom Service Endpoints** - Create custom services for advanced control
- ⏳ **Light Effects** - Transitions, scenes, animations
- ⏳ **Fan Speed Control** - Advanced HVAC systems with variable fan speed
- ⏳ **Preset Temperature Scheduling** - Automatic temperature changes by time

---

## Installation

### Prerequisites
- **Home Assistant** 2024.10 or later
- **Amazon Account** with Alexa app and devices registered
- **Amazon Developer Account** with "Alexa Smart Home Skill" created (see setup guide)
- **HTTPS** configured on your Home Assistant instance

### Setup Instructions

#### Step 1: Create Amazon Developer Resources
1. Go to [Amazon Developer Console](https://developer.amazon.com)
2. Create a new "Alexa Smart Home Skill"
3. Configure OAuth redirect URL to: `https://YOUR_HA_URL/auth/callback`
4. Save your Client ID and Client Secret

#### Step 2: Install Integration
1. In Home Assistant: **Settings → Devices & Services → Create Integration**
2. Search for **"Alexa OAuth2"** and select it
3. Paste your Client ID and Client Secret
4. Click **Submit**

#### Step 3: Authorize with Amazon
1. Home Assistant redirects you to Amazon login
2. Log in with your Amazon account
3. Authorize Home Assistant access to your Alexa devices
4. Amazon redirects you back to Home Assistant
5. **Done!** Devices will appear automatically

---

## Features & Architecture

### Automatic Device Discovery
- Devices auto-discovered on startup
- New devices detected every 15 minutes
- Removed devices cleaned up automatically
- No manual configuration needed

### Real-Time State Synchronization
- Device states update every 5 minutes
- Immediate refresh after control commands (< 2 seconds)
- Unavailable state when device offline
- Coordinator pattern ensures no polling delays

### Responsive Control
- Commands execute within 1-2 seconds
- Immediate UI feedback after control
- State clamping prevents invalid values
- Graceful error handling and retry logic

### Production-Grade Reliability
- **Rate Limiting** - Prevents API throttling with token bucket algorithm
- **Circuit Breaker** - Auto-recovery from persistent API failures
- **Exponential Backoff** - Intelligent retry with jitter
- **Error Handling** - Distinguishes auth errors, rate limits, server errors, network errors
- **Device Registry** - Groups related entities for organization
- **Type Safety** - 100% type hints prevent runtime errors

---

## Code Statistics

| Metric | Value |
|--------|-------|
| **Total Code** | 3,312 lines Python |
| **Modules** | 11 files |
| **Platforms** | 4 (Switch, Light, Climate, Sensor) |
| **Tests** | 155+ comprehensive test cases |
| **Type Coverage** | 100% type hints |
| **Documentation** | All public methods documented |

### File Structure

```
custom_components/alexa/
├── __init__.py              - Integration setup & platform initialization (405 lines)
├── api_client.py            - Alexa Smart Home API client (552 lines)
│   ├── TokenBucket          - Rate limiting
│   ├── CircuitBreaker       - Fault tolerance
│   └── AlexaAPIClient       - HTTP requests with retry logic
├── coordinator.py           - Device polling coordinator (174 lines)
│   └── AlexaDeviceCoordinator - Two-tiered polling
├── switch.py                - Switch platform (216 lines)
│   └── AlexaSwitchEntity    - On/off control
├── light.py                 - Light platform (346 lines)
│   └── AlexaLightEntity     - Brightness/color control
├── climate.py               - Climate platform (358 lines)
│   └── AlexaClimateEntity   - Thermostat control
├── sensor.py                - Sensor platform (270 lines)
│   └── AlexaSensorEntity    - Temperature/humidity/motion/contact/battery
├── models.py                - Data models (247 lines)
│   ├── AlexaDevice          - Device representation
│   ├── AlexaCapability      - Capability representation
│   └── AlexaInterface       - Capability enum
├── config_flow.py           - OAuth2 config flow (293 lines)
│   └── AlexaConfigFlow      - User-facing configuration
├── oauth.py                 - PKCE OAuth2 (366 lines)
│   └── AlexaOAuth2Implementation - Custom OAuth handler
└── const.py                 - Constants & configuration (85 lines)
```

### Platform Mapping Logic

Devices are automatically assigned to the most appropriate platform:

1. **If ThermostatController** → Climate entity (thermostat control)
2. **Else if Brightness/Color/ColorTemp** → Light entity (full color control)
3. **Else if PowerController** → Switch entity (simple on/off)
4. **Plus any applicable sensors** (temperature, motion, contact, battery)

**Result**: One device can create multiple entities (e.g., thermostat + temperature sensor + motion sensor = 3 HA entities)

---

## Configuration

No additional configuration required after installation. The integration automatically:
- ✅ Authenticates with Amazon using OAuth2
- ✅ Discovers all Alexa devices
- ✅ Creates Home Assistant entities
- ✅ Manages state synchronization
- ✅ Handles token refresh

---

## Usage Examples

### Control a Light
```yaml
# Turn on living room light to 75% brightness
service: light.turn_on
target:
  entity_id: light.living_room_light
data:
  brightness: 191  # 75% of 254

# Set light to warm white (color temperature)
service: light.turn_on
target:
  entity_id: light.kitchen_light
data:
  color_temp: 370  # Mireds (warm white)

# Set light to red with 100% brightness
service: light.turn_on
target:
  entity_id: light.bedroom_light
data:
  hs_color: [0, 100]  # Red
  brightness: 254
```

### Control a Switch
```yaml
# Turn on a smart plug
service: switch.turn_on
target:
  entity_id: switch.coffee_maker

# Turn off a switch
service: switch.turn_off
target:
  entity_id: switch.bedroom_outlet
```

### Control a Thermostat
```yaml
# Set target temperature
service: climate.set_temperature
target:
  entity_id: climate.living_room_thermostat
data:
  temperature: 22  # Celsius

# Set to heating mode
service: climate.set_hvac_mode
target:
  entity_id: climate.living_room_thermostat
data:
  hvac_mode: heat
```

### Monitor Sensors
- **Temperature Sensor**: `sensor.room_temperature` (read-only)
- **Humidity Sensor**: `sensor.room_humidity` (read-only)
- **Motion Sensor**: `binary_sensor.motion_detector` (on/off)
- **Contact Sensor**: `binary_sensor.front_door` (open/close)
- **Battery Sensor**: `sensor.wireless_light_battery` (read-only)

---

## Performance Characteristics

### Polling Frequency
- **Device Discovery**: Every 15 minutes (detects new/removed devices)
- **State Updates**: Every 5 minutes (all device attributes refresh)
- **Command Refresh**: Immediate after user commands (< 2 seconds)

### Response Times
- **Command Execution**: Typically 1-2 seconds end-to-end
- **API Latency**: Usually < 1 second
- **Occasional Delays**: Up to 5 minutes if polling happens to be running

### API Rate Limiting
- **Sustained Rate**: 10 requests/second
- **Burst Capacity**: 20 requests initially
- **Per Device**: ~1-2 requests/minute
- **50 Devices**: ~2-3 requests/minute total (well within limits)

### Memory Usage
- **Per Device**: ~2KB (state + metadata)
- **50 Devices**: ~100KB coordinator cache
- **Per Entity**: ~500 bytes (reference to device)
- **Total for 200 Entities**: ~100-200KB additional

---

## Troubleshooting

### Devices Not Discovered
1. Check Home Assistant logs: **Settings → Developer Tools → Logs**
2. Verify Amazon account is logged into Alexa app
3. Ensure devices are enabled in Alexa app
4. Try manual refresh: **Settings → Devices & Services → Alexa OAuth2 → ⋮ → Reload**

### Entities Unavailable
1. Check device status in Alexa app
2. Verify WiFi connectivity for device
3. Check if device is online in Alexa app
4. Wait 5 minutes for state update

### Control Commands Not Working
1. Verify device responds to control in Alexa app
2. Check Home Assistant logs for API errors
3. Verify rate limiting not exceeded (check logs)
4. Try manual device refresh

### Authentication Errors
1. Verify Client ID and Client Secret are correct
2. Check redirect URL matches Amazon Developer settings
3. Try removing and re-adding integration
4. Verify Amazon account hasn't changed password

---

## Development

### Testing
```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_light.py

# Run with coverage
pytest tests/ --cov=custom_components.alexa
```

### Code Quality
- **Type Checking**: `mypy custom_components/alexa/`
- **Linting**: `ruff check custom_components/alexa/`
- **Formatting**: `ruff format custom_components/alexa/`

### Architecture Notes

The integration follows Home Assistant's best practices:

1. **DataUpdateCoordinator Pattern** - Uses HA's built-in coordinator for polling
2. **CoordinatorEntity Pattern** - Entities auto-update when coordinator refreshes
3. **Device Registry** - Entities link to device registry for grouping
4. **Entity Registry** - Supports user customization and persistence
5. **ConfigEntry** - Secure credential storage and lifecycle management

---

## Security

- ✅ **No Plain-Text Credentials** - Tokens encrypted by Home Assistant
- ✅ **RFC 7636 PKCE** - Proof key for code exchange prevents authorization code interception
- ✅ **Automatic Token Refresh** - Tokens refreshed before expiry by Home Assistant framework
- ✅ **HTTPS Required** - All communication encrypted
- ✅ **Proper Error Handling** - Auth failures trigger secure reauth flow
- ✅ **Type Safe** - Type hints prevent entire classes of runtime errors

### Reporting Security Issues

Found a vulnerability? Please report privately to: **security@jasonhollis.com**

Do not open public GitHub issues for security vulnerabilities.

---

## Roadmap

### Phase 1: OAuth2 Authentication ✅ Complete
Secure authentication foundation with token management

### Phase 2: Device Discovery ✅ Complete
Automatic device detection with state polling

### Phase 3: Device Entities ✅ Complete
Switch, Light, Climate, and Sensor platforms with full control

### Phase 4: Automation Integration ⏳ Planned (v1.0.0)
- Webhook-based event triggers
- Automation trigger support for device events
- Custom service endpoints
- Advanced light effects and transitions
- Fan speed control for HVAC

---

## Contributing

Contributions welcome! Please:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Make changes** and add tests
4. **Run tests** to ensure nothing breaks
5. **Commit** with clear message (`git commit -m 'Add amazing feature'`)
6. **Push** to your branch (`git push origin feature/amazing-feature`)
7. **Open Pull Request** with detailed description

### Development Setup

```bash
# Clone repository
git clone https://github.com/jasonhollis/alexa-oauth2.git
cd alexa-oauth2

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements-dev.txt

# Run tests
pytest tests/

# Run linting
ruff check custom_components/alexa/
```

---

## Reporting Issues

Found a bug? Help improve the integration:

1. **Check existing issues**: [GitHub Issues](https://github.com/jasonhollis/alexa-oauth2/issues)
2. **Provide details**:
   - Home Assistant version
   - Integration version
   - Steps to reproduce
   - Home Assistant logs (Settings → Developer Tools → Logs)
   - Which devices affected
3. **Create GitHub issue** with information

**Include logs!** Logs help diagnose issues quickly.

---

## FAQ

**Q: Why do I need an Amazon Developer Account?**
A: The integration uses the Alexa Smart Home Skill API, which requires creating a skill in the Developer Console.

**Q: Can I control devices from outside my home?**
A: Yes, the integration uses the cloud-based Alexa API. Commands work from anywhere with internet access.

**Q: Why does device discovery take 15 minutes?**
A: To balance API usage with responsiveness. Full discovery every 15 minutes detects new/removed devices, while state updates every 5 minutes keep current devices responsive.

**Q: What about Media Players (Echo devices)?**
A: Not yet implemented (Phase 4+). Currently supports switches, lights, thermostats, and sensors.

**Q: Can I use this without the official Alexa media_player integration?**
A: Yes, this integration is completely independent. You can use it alone or alongside other integrations.

**Q: Is this official Amazon software?**
A: No, this is a community integration developed independently.

---

## Support

- **Issues & Bugs**: [GitHub Issues](https://github.com/jasonhollis/alexa-oauth2/issues)
- **Security**: [security@jasonhollis.com](mailto:security@jasonhollis.com)
- **Documentation**: [GitHub Wiki](https://github.com/jasonhollis/alexa-oauth2/wiki)

---

## License

MIT License - See [LICENSE](LICENSE) file for details

---

## Changelog

### Version 0.3.0 (2025-11-13)
- ✅ Light platform with brightness, color, and color temperature control
- ✅ Climate platform with thermostat and HVAC mode control
- ✅ Sensor platform for temperature, humidity, motion, contact, and battery sensors
- ✅ Extended API client with new control methods
- ✅ 155+ comprehensive test cases
- ✅ Complete documentation updates
- **Ready for user testing and feedback**

### Version 0.2.0 (2025-11-10)
- ✅ Device discovery via Alexa Smart Home API
- ✅ AlexaDeviceCoordinator with two-tiered polling
- ✅ Switch platform for PowerController devices
- ✅ Rate limiting and circuit breaker patterns
- ✅ Comprehensive error handling and retry logic

### Version 0.1.0 (2025-11-08)
- ✅ OAuth2 authentication with PKCE
- ✅ Automatic token refresh
- ✅ Multi-account support
- ✅ Encrypted token storage

---

**Version**: 0.3.0
**Last Updated**: 2025-11-13
**Status**: Phase 3 Complete - Ready for Testing
**Next Phase**: Phase 4 (Automation Triggers) - Coming Soon
