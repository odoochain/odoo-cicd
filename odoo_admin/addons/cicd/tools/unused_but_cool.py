
def _delete_dockercontainers(name):
    containers = _get_docker().containers.list(all=True, filters={'name': [name]})
    for container in containers:
        if container.status == 'running':
            container.kill()
        container.remove(force=True)
