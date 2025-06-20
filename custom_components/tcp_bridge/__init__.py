"""
MIT License

Copyright (c) 2023 Your Name/Organization

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
# <your_ha_config_directory>/custom_components/tcp_bridge/__init__.py
import asyncio
import json
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.const import (
    CONF_PORT,
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
    EVENT_STATE_CHANGED,
)
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DOMAIN = "tcp_bridge"
CONF_MONITORED_ENTITIES = "monitored_entities"
CONF_KEY_VAL = "key_val"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_PORT): cv.port,
                vol.Optional(CONF_MONITORED_ENTITIES, default=[]): vol.All(cv.ensure_list, [cv.string]),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

VAL_KEY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_KEY_VAL): cv.string
    }
)

ENTITY_ID_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): vol.All(cv.ensure_list, [cv.string])
    }
)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """
    Set up the TCP Bridge component from configuration.yaml.

    This function initializes the TCP server, registers state change listeners,
    and exposes Home Assistant services for interacting with the bridge.

    Args:
        hass (HomeAssistant): The Home Assistant core object.
        config (ConfigType): The configuration dictionary for the component.

    Returns:
        bool: True if the component was successfully set up, False otherwise.
    """
    _LOGGER.info(f"Starting TCP Bridge setup")
    conf = config.get(DOMAIN)
    if not conf:
        _LOGGER.info("TCP Bridge not configured. Skipping setup.")
        return True # Return true even if not configured to not break HA startup for other components

    hass.data[DOMAIN] = {}
    port = conf[CONF_PORT]
    monitored_entities = conf.get(CONF_MONITORED_ENTITIES, [])
    hass.data[DOMAIN]['monitored_entities'] = monitored_entities

    _LOGGER.info(f"Starting TCP Bridge on port {port}")

    # Create a task for the server so it runs in the background
    # and async_setup can return True to HA quickly.
    hass.data[DOMAIN]['connected_clients'] = [] # List to hold connected client writers
    _LOGGER.info("server_instance_task.")
    server_instance_task = asyncio.create_task(start_server_and_log(port, hass))
    hass.data[DOMAIN]['server_task'] = server_instance_task # This task wraps start_server_and_log

    _LOGGER.info("async_listen_once.")
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, cleanup_server(server_instance_task))
    _LOGGER.info("async_listen_once2.")

    async def async_state_listener(event: dict):
        """
        Listen for state changes and send updates to connected clients.

        This callback is triggered when an entity's state changes. If the entity
        is in the monitored list, its new state and changed attributes are sent
        to all connected TCP clients.

        Args:
            event (dict): The event data containing old and new state information.
        """
        new_state = event.data.get('new_state')
        if not new_state:
            return  # No new state to process

        monitored_entities = hass.data[DOMAIN].get('monitored_entities', [])

        # Only send update if the entity is in the monitored list
        if new_state.entity_id in monitored_entities:
            _LOGGER.info(f"Got new state for {new_state.entity_id}.")
            old_state = event.data.get('old_state')
            await _send_state_update_to_clients(hass, new_state.entity_id, old_state, new_state)

    async def start_pubsub(_event):
        """
        Register the state change listener once Home Assistant has started.

        This ensures that the TCP bridge only starts sending state updates
        after Home Assistant is fully operational.

        Args:
            _event: The Home Assistant started event (unused).
        """
        _LOGGER.info("async_listen.")
        hass.bus.async_listen(EVENT_STATE_CHANGED, async_state_listener)
        _LOGGER.info("async_listen done.")

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start_pubsub)
    _LOGGER.info("TCP Bridge setup complete.")

    async def update_all_monitored(call):
        """
        Home Assistant service to force an update of all monitored entities.

        This service sends the current state and attributes of all entities
        configured in `monitored_entities` to all connected TCP clients.
        Useful for clients that connect and need an immediate full state sync.

        Args:
            call: The service call object (unused for data, but required by HA).
        """
        _LOGGER.debug(f"Updating all entities")
        monitored_entities = hass.data[DOMAIN].get('monitored_entities', [])

        for entity_id in monitored_entities:
            state = hass.states.get(entity_id)
            if not state:
                _LOGGER.debug(f"Entity {entity_id} not found in Home Assistant states.")
                continue
            await _send_state_update_to_clients(hass, entity_id, None, state)

    hass.services.async_register(
        DOMAIN,
        "update_all_monitored",
        update_all_monitored,
    )

    async def send_key_val(call):
        """
        Home Assistant service to send a custom key-value string to TCP clients.

        This service allows Home Assistant automations or scripts to send
        arbitrary string messages to all connected TCP clients.

        Args:
            call: The service call object, containing the 'key_val' data.
        """
        _LOGGER.debug(f"Sending: ", str(call.data[CONF_KEY_VAL]))
        await _send_message_to_clients(hass, str(call.data[CONF_KEY_VAL]))

    hass.services.async_register(
        DOMAIN,
        "send_key_val",
        send_key_val,
        schema=VAL_KEY_SCHEMA
    )

    async def add_monitored_entities(call):
        """
        Home Assistant service to add new entities to the monitored_entities list.

        Args:
            call: The service call object, containing the 'entity_id' data (list of entity IDs).
        """
        entity_ids_to_add = call.data.get("entity_id")
        if not entity_ids_to_add:
            _LOGGER.error("Attempted to add entities without providing 'entity_id'.")
            return

        monitored_entities = hass.data[DOMAIN].get('monitored_entities', [])
        for entity_id_to_add in entity_ids_to_add:
            if entity_id_to_add not in monitored_entities:
                monitored_entities.append(entity_id_to_add)
                _LOGGER.info(f"Added entity '{entity_id_to_add}' to monitored_entities.")
            else:
                _LOGGER.debug(f"Entity '{entity_id_to_add}' is already in monitored_entities. No action taken.")
        hass.data[DOMAIN]['monitored_entities'] = monitored_entities

    hass.services.async_register(
        DOMAIN,
        "add_monitored_entities",
        add_monitored_entities,
        schema=ENTITY_ID_SCHEMA
    )

    async def remove_monitored_entities(call):
        """
        Home Assistant service to remove entities from the monitored_entities list.

        Args:
            call: The service call object, containing the 'entity_id' data (list of entity IDs).
        """
        entity_ids_to_remove = call.data.get("entity_id")
        if not entity_ids_to_remove:
            _LOGGER.error("Attempted to remove entities without providing 'entity_id'.")
            return

        monitored_entities = hass.data[DOMAIN].get('monitored_entities', [])
        for entity_id_to_remove in entity_ids_to_remove:
            if entity_id_to_remove in monitored_entities:
                monitored_entities.remove(entity_id_to_remove)
                _LOGGER.info(f"Removed entity '{entity_id_to_remove}' from monitored_entities.")
            else:
                _LOGGER.debug(f"Entity '{entity_id_to_remove}' is not in monitored_entities. No action taken.")
        hass.data[DOMAIN]['monitored_entities'] = monitored_entities

    hass.services.async_register(
        DOMAIN,
        "remove_monitored_entities",
        remove_monitored_entities,
        schema=ENTITY_ID_SCHEMA
    )

    async def get_entity(call):
        """
        Home Assistant service to retrieve the current state of a specific entity.

        This service sends the current state and attributes of the specified entity
        to all connected TCP clients. Useful for clients that need an immediate
        state sync for a particular entity.

        Args:
            call: The service call object, containing the 'entity_id' data.
        """
        entity_ids_to_get = call.data.get("entity_id")
        if not entity_ids_to_get:
            _LOGGER.error("Attempted to get an entity without providing 'entity_id'.")
            return

        for entity_id_to_get in entity_ids_to_get:
            state = hass.states.get(entity_id_to_get)
            if not state:
                _LOGGER.debug(f"Entity {entity_id_to_get} not found in Home Assistant states. Cannot send update.")
                return

            _LOGGER.info(f"Sending state of entity '{entity_id_to_get}'.")
            await _send_state_update_to_clients(hass, entity_id_to_get, None, state)

    hass.services.async_register(
        DOMAIN,
        "get_entity",
        get_entity,
        schema=ENTITY_ID_SCHEMA
    )

    return True # Return True to indicate successful setup


async def _send_state_update_to_clients(hass: HomeAssistant, entity_id: str, old_state: dict, new_state: dict):
    """
    Helper function to format and send state updates to connected clients.

    This function constructs a message containing changed state and attribute
    information for a given entity and sends it to all currently connected
    TCP clients.

    Args:
        hass (HomeAssistant): The Home Assistant core object.
        entity_id (str): The entity ID whose state is being updated.
        old_state (dict): The previous state object of the entity.
        new_state (dict): The current state object of the entity.
    """
    message_lines = []

    # Add state if it changed or if there was no old state
    if old_state is None or old_state.state != new_state.state:
        state_line = f"{new_state.entity_id}={new_state.state}"
        message_lines.append(state_line)
        _LOGGER.debug(f"Adding state to message: {state_line}")

    # Add attributes
    for key, new_val in new_state.attributes.items():
        old_val = None
        if old_state and old_state.attributes:
            old_val = old_state.attributes.get(key)

        # Only add attribute if it's new or its value has changed
        if old_val is None or new_val != old_val:
            # Ensure value is a string for consistent output, especially for complex types.
            # Use json.dumps for non-primitive types to ensure proper serialization.
            if isinstance(new_val, (dict, list, bool, type(None))):
                formatted_val = json.dumps(new_val)
            else:
                formatted_val = str(new_val)
            attribute_line = f"{new_state.entity_id}.{key}={formatted_val}"
            message_lines.append(attribute_line)
            _LOGGER.debug(f"Adding attribute to message: {attribute_line}")

    if not message_lines:  # If no state or attributes were added, do nothing
        return

    message = "\n".join(message_lines) + "\n"  # Join lines and add final newline

    await _send_message_to_clients(hass, message)

async def _send_message_to_clients(hass: HomeAssistant, message: str):
    """
    Send a message to all connected TCP clients and handle disconnections.

    Iterates through the list of connected clients and attempts to send the
    provided message. Clients that encounter connection errors are removed
    from the list.

    Args:
        hass (HomeAssistant): The Home Assistant core object.
        message (str): The string message to send to clients.
    """
    clients_to_remove = []
    for writer in list(hass.data[DOMAIN].get('connected_clients', [])):
        try:
            writer.write(message.encode())
            await writer.drain()
        except ConnectionResetError:
            _LOGGER.info(f"Client connection reset while sending state update.")
            clients_to_remove.append(writer)
        except Exception as e:
            _LOGGER.exception(f"Error sending state update to client: {e}")
            clients_to_remove.append(writer)

    # Remove disconnected clients
    for writer in clients_to_remove:
        if writer in hass.data[DOMAIN].get('connected_clients', []):
            hass.data[DOMAIN]['connected_clients'].remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass  # Ignore errors during closing already broken connections

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, hass: HomeAssistant):
    """
    Handle incoming client connections.

    This function manages the lifecycle of a single client connection. It
    receives commands (JSON payloads) from the client, executes the
    corresponding Home Assistant services, and sends back a response.
    It also handles client disconnections.

    Args:
        reader (asyncio.StreamReader): The stream reader for the client connection.
        writer (asyncio.StreamWriter): The stream writer for the client connection.
        hass (HomeAssistant): The Home Assistant core object.
    """
    peername = writer.get_extra_info('peername')
    _LOGGER.info(f"Client connected: {peername}")
    hass.data[DOMAIN]['connected_clients'].append(writer)
    _LOGGER.debug(f"Added client {peername} to connected_clients list.")

    try:
        while True:
            #message_bytes = await reader.readuntil(b'\n')
            message_bytes = await reader.read(1024)
            if not message_bytes:
                _LOGGER.info(f"Client {peername} disconnected (no data).")
                break

            message = message_bytes.decode().strip()
            _LOGGER.debug(f"Received from {peername}: {message}")

            response_payload = {}
            try:
                # Expected JSON command format:
                # {
                #   "domain": "light",
                #   "service": "toggle",
                #   "entity_id": "light.living_room",
                #   "data": {"brightness": 200} (optional service specific data)
                # }
                command_data = json.loads(message)

                service_domain = command_data.get("domain")
                service_name = command_data.get("service")

                # Construct service_data for hass.services.async_call
                # It includes entity_id and other parameters directly
                payload_for_ha_service = {}

                # Get entity_id: from command, or default, or none if not provided
                entity_id_val = command_data.get("entity_id", None)
                if entity_id_val:
                     payload_for_ha_service["entity_id"] = entity_id_val

                # Add other data
                if "data" in command_data and isinstance(command_data["data"], dict):
                    payload_for_ha_service.update(command_data["data"])

                if not service_domain or not service_name:
                    raise ValueError("Missing 'domain' or 'service' in command.")

                _LOGGER.info(
                    f"Executing service: {service_domain}.{service_name} "
                    f"with data: {payload_for_ha_service}"
                )

                # Call Home Assistant service
                await hass.services.async_call(
                    service_domain,
                    service_name,
                    service_data=payload_for_ha_service, # Pass the constructed payload
                    blocking=False, # Wait for service to complete. Consider False for fire-and-forget.
                    # context=None # Can create a context if needed
                )
                #response_payload = {"status": "success", "message": f"Service {service_domain}.{service_name} called."}
                response_payload = f"response=success (Service {service_domain}.{service_name} called)"
                _LOGGER.info(f"Service {service_domain}.{service_name} executed successfully.")

            except json.JSONDecodeError:
                _LOGGER.error(f"Invalid JSON received from {peername}: {message}")
                #response_payload = {"status": "error", "message": "Invalid JSON format"}
                response_payload = f"response=error (Invalid JSON format)"
            except ValueError as ve:
                _LOGGER.error(f"Command error from {peername}: {ve} (Command: {message})")
                #response_payload = {"status": "error", "message": str(ve)}
                response_payload = f"response=error ({str(e)})"
            except Exception as e:
                _LOGGER.exception(f"Error processing command from {peername}: {message}")
                #response_payload = {"status": "error", "message": f"Internal server error: {str(e)}"}
                response_payload = f"response=error ({str(e)})"

            #writer.write(json.dumps(response_payload).encode() + b'\n')
            writer.write(response_payload.encode() + b'\n')
            await writer.drain()

    except ConnectionResetError:
        _LOGGER.info(f"Client {peername} connection reset.")
    except asyncio.CancelledError:
        _LOGGER.info(f"Client handler for {peername} cancelled.")
        raise # Propagate cancellation
    except Exception as e:
        _LOGGER.exception(f"Unexpected error in client handler for {peername}: {e}")
    finally:
        _LOGGER.info(f"Closing connection with {peername}")
        if writer in hass.data[DOMAIN].get('connected_clients', []):
            hass.data[DOMAIN]['connected_clients'].remove(writer)
            _LOGGER.debug(f"Removed client {peername} from connected_clients list.")
        writer.close()
        await writer.wait_closed()

async def start_server_and_log(port: int, hass: HomeAssistant):
    """
    Start the TCP server and keep it running.

    This function initializes the asyncio TCP server, binds it to the specified
    port, and starts serving client connections. It logs server status and
    handles server-related errors and cancellations.

    Args:
        port (int): The port number on which the server will listen.
        hass (HomeAssistant): The Home Assistant core object.
    """
    try:
        server = await asyncio.start_server(lambda r, w: handle_client(r, w, hass), '0.0.0.0', port)
        hass.data[DOMAIN]['server_serve_forever_task'] = asyncio.create_task(server.serve_forever()) # Store the serve_forever task
        addr = server.sockets[0].getsockname()
        _LOGGER.info(f"TCP Bridge server listening on {addr}")
        # Wait for the server to be cancelled
        await hass.data[DOMAIN]['server_serve_forever_task']
    except asyncio.CancelledError:
        _LOGGER.info("TCP server task was cancelled.")
    except OSError as e:
        _LOGGER.error(f"Failed to start TCP server on port {port}: {e}")
        # If the server fails to start, we don't want to block HA startup indefinitely
        # So, we log the error and the component setup will complete (returning True)
        # but the server won't be running.
    finally:
        if 'server' in locals() and server.is_serving():
            _LOGGER.info("Closing TCP server...")
            server.close()
            await server.wait_closed()
            _LOGGER.info("TCP server closed.")

async def cleanup_server(server_task_to_cancel: asyncio.Task):
    """
    Clean up server resources on Home Assistant stop.

    This function is called when Home Assistant is shutting down. It cancels
    the main TCP server task to gracefully close connections and release
    resources.

    Args:
        server_task_to_cancel (asyncio.Task): The asyncio task representing
                                               the running TCP server.
    """
    _LOGGER.info("Home Assistant is stopping. Shutting down TCP Bridge server.")
    if server_task_to_cancel and not server_task_to_cancel.done():
        server_task_to_cancel.cancel()
        try:
            await server_task_to_cancel # Wait for the server task to finish cancellation
        except asyncio.CancelledError:
            _LOGGER.info("Server task successfully cancelled during cleanup.")
