import argparse
import logging
import sys
import socket
import json

SOCKET_PATH = "/opt/healthagent/run/health.sock"
MESSAGE_SIZE = 4096

def get_response(command, timeout):
    try:
        # Create a Unix domain socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client_socket:
            client_socket.settimeout(timeout)
            # Connect to the Unix socket
            client_socket.connect(SOCKET_PATH)

            # Prepare the message
            client_socket.sendall(command.encode())
            client_socket.shutdown(socket.SHUT_WR)
            # Now wait for full response
            response = b''
            while True:
                chunk = client_socket.recv(MESSAGE_SIZE)
                if not chunk:
                    # Server closed connection
                    break
                response += chunk
            try:
                return json.loads(response.decode())
            except json.JSONDecodeError as e:
                logging.error(f"Unable to parse json: {response}")
                return None
            except Exception as e:
                logging.exception(e)
                return None

    except (ConnectionRefusedError, FileNotFoundError) as e:
        logging.error("Connection to Healthagent could not be established, is Healthagent running?")
        return None
    except socket.timeout as e:
        logging.error("Socket Timed out!!")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return None

def print_bash_friendly(result):
    pass

def run_command(command, timeout):
    response = get_response(command=command, timeout=timeout)
    if not response:
        sys.exit(-1)
    print(json.dumps(response, indent=4))

def main():

    # Set up argument parser
    parser = argparse.ArgumentParser(description="Healthagent Client")

    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "-e", "--epilog", action="store_true", help="Run the epilog/post-job validation healthchecks"
    )
    group.add_argument(
        "-p", "--prolog", action="store_true", help="Run the prolog/pre-job validation healthchecks"
    )
    group.add_argument(
        "-s", "--status", action="store_true", help="Get the current health status of the node"
    )

    parser.add_argument("-b", "--bash", action="store_true", default=False, help="Export results into bash friendly variables")
    #parser.add_argument(
    #    "-l", "--log-level", help="Logging level (e.g., DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    #)

    args = parser.parse_args()

    # Configure logging level
    #log_level = args.log_level.upper() if args.log_level else "ERROR"
    #if log_level not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
    #    log_level = "ERROR"
    logging.basicConfig(
        format='%(levelname)s - %(message)s',
        level=logging.ERROR
        #level=getattr(logging, log_level, logging.ERROR)
        )

    if not (args.epilog or args.prolog or args.status):
        args.status = True
    # Handle arguments
    if args.epilog:
        run_command(command="epilog", timeout=1200)
    elif args.prolog:
        pass
    elif args.status:
        run_command(command="status", timeout=30)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
