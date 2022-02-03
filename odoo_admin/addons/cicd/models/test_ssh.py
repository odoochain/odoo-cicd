from pssh.clients import SSHClient
from pssh.exceptions import Timeout
import gevent
import gevent.lock

import shlex
import time
import logging
from pathlib import PurePath

#logging.basicConfig(level=logging.DEBUG)

hosts = 'localhost'
client = SSHClient(hosts)

remote_echo_path = str(PurePath('/tmp', 'echowrite.py'))
client.scp_send('echowrite.py', remote_echo_path)

commands = [
        'python3 /tmp/echowrite.py 5',
        'python3 /tmp/echowrite.py 30',
        f'rm {remote_echo_path}'
        ]
command_timeout=10

wait = gevent.lock.BoundedSemaphore(1)


def evntlet_add_msg(msgs, src, pf):
    global wait
    print(pf)
    for msg in src:
        print(pf + msg)
        with wait:
            msgs.append(pf + msg)
        # oder: send to log.io
        gevent.sleep(.1)


for command in commands:
    eventlet_msgs = []
    print(f"Running {command} with timeout {command_timeout}")
    host_out = client.run_command(
            command,
            use_pty=True)   # ohne das failed/haengt close_channel
                            # leider kommt dann allels über stdout.
                            # stderr bleibt leer.
    
    rstderr = gevent.spawn(evntlet_add_msg, eventlet_msgs, host_out.stderr, "stderr: ")
    rstdout = gevent.spawn(evntlet_add_msg, eventlet_msgs, host_out.stdout, "stdout: ")

    try:
        client.wait_finished(host_out, command_timeout)
    except Timeout:
        print("Timeout occured")
        gevent.killall([rstdout, rstderr])
        host_out.client.close_channel(host_out.channel)
        print("Channel closed")
    
    print(f"Messages: {eventlet_msgs}")
    print(f"Command {command} exited with {host_out.exit_code}")
