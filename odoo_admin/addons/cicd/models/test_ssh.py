from pssh.utils import enable_host_logger

from pssh.clients import SSHClient
from pssh.exceptions import Timeout
import shlex

hosts = 'localhost'
client = SSHClient(hosts)

import pudb;pudb.set_trace()
enable_host_logger()

commands = [
        #'cd /tmp; pwd'
        # 'echo "a";sleep 2; pwd'
        '/home/cicd/cicd_app/odoo_admin/addons/cicd/models/test_ssh_producer.py',
        #'HALLO=test; echo "a"; echo "$HALLO"',
        # 'while true; do echo a line; sleep .1; done'
        #, 'echo I am done'
        #, 'echo I am unhappy && exit 120'
        #, 'echo I am unhappy and timing out && while true; do echo a line; sleep .1; done && exit 120'
        #, shlex.join(['echo', 'cooler parameter', 'oof, hakeliger | mist']) + " >> crazy.text"
        ]


for with_stdout in [True, False]:
    for command in commands:
        print(f"\nRunning {command} with stdout {with_stdout}")
        if with_stdout:
            output = client.run_command(
                command,
                use_pty=True, timeout=2, read_timeout=2)

            # Read as many lines of output as hosts have sent before the timeout
            import pudb;pudb.set_trace()
            stdout = []
            try:
                for line in output.stdout:
                    stdout.append(line)
                for line in output.stdout:
                    stdout.append(line)
            except Timeout:
                print("Timeout occured")

            # Closing channel which has PTY has the effect of terminating
            # any running processes started on that channel.
            for host_out in output:
                host_out.client.close_channel(host_out.channel)
            # Join is not strictly needed here as channel has already been closed and
            # command has finished, but is safe to use regardless.
            client.join(output)
            # Can now read output up to when the channel was closed without blocking.
            rest_of_stdout = list(output[0].stdout)

            print(f"Stdout vor timeout: {stdout}")
            print(f"Stdout nach timeout: {rest_of_stdout}")
            exit_codes = [o.exit_code for o in output]
            print(f"Exit codes: {exit_codes}")

        else:
            output = client.run_command(
            command,
            use_pty=True)
            try:
                client.join(output, timeout=1)
            except:
                for host_out in output:
                    host_out.client.close_channel(host_out.channel)
                print("Timeout occured, exit codes discarded!")
            else:
                exit_codes = [o.exit_code for o in output]
                print(f"Exit codes: {exit_codes}")

 


