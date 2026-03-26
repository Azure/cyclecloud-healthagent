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

            # Always send JSON
            client_socket.sendall(json.dumps(command).encode())
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

def parse_check_args(check_groups):
    """Parse -c groups into a checks dict.

    Each group is a list of strings: [check_name, key=value, ...]

    Returns:
        dict or None: {check_name: {key: value, ...}, ...} or None if no checks specified.
    """
    if not check_groups:
        return None
    checks = {}
    for group in check_groups:
        if not group:
            continue
        check_name = group[0]
        kwargs = {}
        for arg in group[1:]:
            if '=' in arg:
                key, _, value = arg.partition('=')
                # Comma-separated values become a list
                if ',' in value:
                    value = value.split(',')
                kwargs[key] = value
            else:
                logging.error(f"Invalid argument '{arg}' for check '{check_name}'. Expected key=value format.")
                sys.exit(1)
        checks[check_name] = kwargs
    return checks if checks else None

def print_bash_friendly(result):

    res = {}
    for module_name, checks in result.items():
        total_errors = sum(
            check.get('error_count', 1)
            for check in checks.values()
            if check.get('status') == 'Error'
        )
        res[module_name] = total_errors
    return [print(f"{x},{y}") for x,y in res.items()]

def print_checks_table(response, check_type="all"):
    """Format list_checks response as a table, optionally filtered by type."""
    rows = []
    for module, checks in response.items():
        for name, info in checks.items():
            categories = info.get("category", [])
            if check_type != "all" and check_type not in categories:
                continue
            category = ", ".join(categories)
            interval = info.get("interval")
            if isinstance(interval, int) and interval > 0:
                interval_str = f"{interval}s"
            elif interval == -1:
                interval_str = "-1"
            elif interval == "async":
                interval_str = "async"
            else:
                interval_str = ""
            args_str = ", ".join(info.get("args", []))
            desc = info.get("description", "")
            rows.append((module, name, category, interval_str, args_str, desc))

    if not rows:
        print("No checks found.")
        return

    headers = ("Module", "Check", "Category", "Interval", "Args", "Description")
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in col_widths)))
    for row in rows:
        print(fmt.format(*row))

def run_command(command, timeout, bash=False):
    response = get_response(command=command, timeout=timeout)
    if not response:
        sys.exit(-1)
    if bash:
        return print_bash_friendly(response)
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
    group.add_argument(
        "-v", "--version", action="store_true", help="Return Healthagent version"
    )
    group.add_argument(
        "-l", "--list-checks", metavar="TYPE", nargs="?", const="all",
        choices=["all", "epilog", "prolog"],
        help="List available checks by type (default: all). Example: health -l epilog"
    )

    parser.add_argument(
        "-c", "--check", action="append", nargs="+", metavar="NAME",
        help="Run a prolog/epilog check by name, with optional key=value args. "
             "Repeatable. Example: -c GpuMemoryCheck gpu_id=0,1 -c GpuDiagnosticCheck"
    )
    parser.add_argument("-b", "--bash", action="store_true", default=False, help="Export results into bash friendly variables")

    args = parser.parse_args()

    logging.basicConfig(
        format='%(levelname)s - %(message)s',
        level=logging.ERROR
        )

    if not (args.epilog or args.prolog or args.version or args.list_checks):
        args.status = True

    checks = parse_check_args(args.check)

    if checks and not (args.epilog or args.prolog):
        parser.error("-c/--check can only be used with -e/--epilog or -p/--prolog")

    if args.list_checks:
        command = {"command": "list_checks", "type": "all"}
        response = get_response(command=command, timeout=10)
        if not response:
            sys.exit(-1)
        print_checks_table(response, check_type=args.list_checks)
    elif args.epilog:
        command = {"command": "epilog"}
        if checks:
            command["checks"] = checks
        run_command(command=command, timeout=1200)
    elif args.prolog:
        command = {"command": "prolog"}
        if checks:
            command["checks"] = checks
        run_command(command=command, timeout=1200)
    elif args.status:
        run_command(command={"command": "status"}, timeout=30, bash=args.bash)
    elif args.version:
        run_command(command={"command": "version"}, timeout=5)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
