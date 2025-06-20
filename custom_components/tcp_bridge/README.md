# Home Assistant TCP Bridge Custom Component

**⚠️ WARNING: Security Advisory ⚠️**
This component creates an unencrypted and unauthenticated TCP server. It is designed for local network use only. **DO NOT expose the configured port to the public internet** without implementing proper security measures (e.g., VPN, SSH tunnel, or a secure proxy). Exposing this port directly to the internet can lead to unauthorized access and control of your Home Assistant instance. You have been warned!

I wote this custom component to interface with my Crestron controller. A sample Crestron module and app is also provided. But the module is not specific to Crestron and can be used with any TCP client (Python example is provided in this documentation as well).

At a high-level, this custom component for Home Assistant establishes a TCP server that allows external applications to interact with Home Assistant. It provides a bridge for:
1.  Receiving real-time state updates for specified Home Assistant entities.
2.  Sending commands to Home Assistant services (e.g., turning lights on/off, setting thermostat temperatures).
3.  Sending custom key-value messages from Home Assistant to connected TCP clients.

## Features

*   **Bi-directional Communication**: Send state updates from Home Assistant to TCP clients and receive commands from clients to execute Home Assistant services.
*   **Configurable Port**: Specify the TCP port for the server.
*   **Monitored Entities**: Define a list of entities whose state changes will be pushed to connected clients.
*   **Service Call Execution**: Clients can send JSON payloads to trigger any Home Assistant service.
*   **Custom Message Sending**: Home Assistant services can send arbitrary key-value strings to connected clients.
*   **Automatic Reconnection Handling**: Basic handling for client disconnections.

## Installation

1.  Copy the `tcp_bridge` folder into your Home Assistant `custom_components` directory.
    Your Home Assistant configuration directory is typically `/config`.
    The path should look like: `<your_ha_config_directory>/custom_components/tcp_bridge/`.
2.  Restart Home Assistant.

## Configuration

Add the following to your `configuration.yaml` file:

```yaml
tcp_bridge:
  port: 8124 # Required: The port for the TCP server to listen on.
  monitored_entities: # Optional: A list of entity IDs to monitor for state changes as soon as the component loads.
    - light.living_room
    - sensor.temperature_outside
    - switch.fan
```

### Configuration Variables

*   **`port`** (Required): The TCP port number on which the bridge server will listen for incoming connections.
*   **`monitored_entities`** (Optional): A list of Home Assistant entity IDs (e.g., `light.living_room`, `sensor.temperature`) that you want to monitor as soon as the component loads. When the state or attributes of any of these entities change, an update will be sent to all connected TCP clients. If not specified, no entities will be monitored by default.

## Usage

### Receiving State Updates

Once configured, the TCP Bridge will automatically send updates to all connected clients whenever a monitored entity's state or attributes change.

The format of the messages sent to clients is a series of `key=value` pairs, one per line, terminated by a newline character.

**Examples:**

*   `light.living_room=on`
*   `sensor.temperature_outside=25.5`
*   `light.living_room.brightness=128`
*   `sensor.temperature_outside.unit_of_measurement=°C`
*   `binary_sensor.motion_detector.last_triggered={"timestamp": "2023-10-27T10:00:00Z", "zone": "hallway"}` (JSON for complex attributes)

Only changed states or attributes are sent. If only an attribute changes, only that attribute line will be sent, not the full state.

### Sending Commands to Home Assistant Services

Connected TCP clients can send JSON payloads to execute Home Assistant services. The server expects a JSON string followed by a newline character.

**JSON Command Format:**

```json
{
  "domain": "light",
  "service": "turn_on",
  "entity_id": "light.living_room",
  "data": {
    "brightness": 200,
    "color_temp": 350
  }
}
```

*   **`domain`** (Required): The domain of the service (e.g., `light`, `switch`, `climate`).
*   **`service`** (Required): The name of the service to call (e.g., `turn_on`, `toggle`, `set_temperature`).
*   **`entity_id`** (Optional): The entity ID(s) on which the service should act (e.g., `light.living_room`). Can be a single string or a list of strings.
*   **`data`** (Optional): A dictionary of service-specific data (e.g., `brightness`, `temperature`, `hvac_mode`).

**Example (Python client):**

```python
import socket
import json

HOST = '127.0.0.1' # Or your Home Assistant IP
PORT = 8124

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.connect((HOST, PORT))
    command = {
        "domain": "light",
        "service": "toggle",
        "entity_id": "light.living_room"
    }
    s.sendall(json.dumps(command).encode() + b'\n')
    response = s.recv(1024).decode().strip()
    print(f"Response from HA: {response}")
```

The TCP Bridge will respond with a simple `response=success (...)` or `response=error (...)` message.

### Home Assistant Services Provided by TCP Bridge

The component exposes the following services that can be called from Home Assistant automations, scripts, or developer tools:

#### `tcp_bridge.update_all_monitored`

This service forces an update for all entities listed in `monitored_entities` to all connected TCP clients. This is useful if a client connects and needs the current state of all relevant entities immediately.

**Example Automation:**

```yaml
- alias: "Send all monitored states on HA start"
  trigger:
    - platform: homeassistant
      event: start
  action:
    - service: tcp_bridge.update_all_monitored
```

#### `tcp_bridge.send_key_val`

This service allows you to send a custom key-value string to all connected TCP clients.

**Service Data:**

```yaml
key_val: "custom_message=Hello from Home Assistant!"
```

**Example Automation:**

```yaml
- alias: "Send custom message to TCP clients"
  trigger:
    - platform: state
      entity_id: binary_sensor.door_sensor
      to: 'on'
  action:
    - service: tcp_bridge.send_key_val
      data:
        key_val: "door_status=open"
```

#### `tcp_bridge.add_monitored_entities`

This service allows you to dynamically add new entities to the list of monitored entities. State changes for these newly added entities will then be pushed to connected TCP clients.

**Service Data:**

```yaml
entity_id:
  - light.kitchen
  - sensor.hallway_motion
```

*   **`entity_id`** (Required): A single entity ID string or a list of entity ID strings to add to the monitored list.

**Example Automation:**

```yaml
- alias: "Add new entities to monitor"
  trigger:
    - platform: event
      event_type: custom_event_add_entities
  action:
    - service: tcp_bridge.add_monitored_entities
      data:
        entity_id:
          - light.kitchen
          - sensor.hallway_motion
```

#### `tcp_bridge.remove_monitored_entities`

This service allows you to dynamically remove entities from the list of monitored entities. Once removed, state changes for these entities will no longer be pushed to connected TCP clients.

**Service Data:**

```yaml
entity_id:
  - light.kitchen
  - sensor.hallway_motion
```

*   **`entity_id`** (Required): A single entity ID string or a list of entity ID strings to remove from the monitored list.

**Example Automation:**

```yaml
- alias: "Remove entities from monitoring"
  trigger:
    - platform: event
      event_type: custom_event_remove_entities
  action:
    - service: tcp_bridge.remove_monitored_entities
      data:
        entity_id: light.kitchen
```

#### `tcp_bridge.get_entity`

This service retrieves the current state of a specific entity and sends it to all connected TCP clients. This is useful for clients that need the current state of a particular entity on demand.

**Service Data:**

```yaml
entity_id: "sensor.my_custom_sensor"
```

*   **`entity_id`** (Required): The entity ID string for which to retrieve the state.

**Example Automation:**

```yaml
- alias: "Get state for specific sensor"
  trigger:
    - platform: time_pattern
      minutes: "/5"
  action:
    - service: tcp_bridge.get_entity
      data:
        entity_id: sensor.my_custom_sensor
```

## Logging

The component logs information and errors to the Home Assistant log. You can adjust the logging level for `tcp_bridge` in your `configuration.yaml` for more detailed debugging:

```yaml
logger:
  default: info
  logs:
    custom_components.tcp_bridge: debug
```

## Troubleshooting

*   **Server not starting**: Check Home Assistant logs for errors related to `tcp_bridge`. Ensure the `port` is not already in use by another application.
*   **No state updates**: Verify that `monitored_entities` is correctly configured and that the entity IDs are accurate. Check Home Assistant logs for any errors during state change processing.
*   **Commands not executing**: Ensure the JSON payload sent from the client is correctly formatted and includes the required `domain` and `service` fields. Check Home Assistant logs for errors related to service calls.
*   **Client connection issues**: Ensure the client is connecting to the correct IP address and port. Check firewall settings on both the Home Assistant machine and the client machine.