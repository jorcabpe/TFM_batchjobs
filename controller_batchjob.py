import kopf
import kubernetes
import kubernetes.config
from kubernetes import client, config
from kubernetes.client import CustomObjectsApi, ApiException
import logging

# Definir las constantes
GROUP = "batch.upv.es"
VERSION = "v1"
QUEUE_PLURAL = "queues"
JOB_PLURAL = "batchjobs"
NAMESPACE  = 'default'

# Configurar logging
logging.basicConfig(level=logging.DEBUG)

kubernetes.config.load_kube_config()
api = CustomObjectsApi()

# Helper para obtener la lista de colas
def get_list_queues(namespace):
    try:
        return api.list_namespaced_custom_object(
            group=GROUP, version=VERSION, namespace=namespace, plural=QUEUE_PLURAL
        )
    except ApiException as e:
        logging.error(f"Exception when calling CustomObjectsApi->list_namespaced_custom_object: {e}")
        return []

# Helper para actualizar el estado de una cola
def update_queue_status(namespace, name, status):

    try:
        api.patch_namespaced_custom_object(
            group=GROUP, version=VERSION, namespace=namespace, plural=QUEUE_PLURAL, name=name, 
            body=status
        )    
    except ApiException as e:
        logging.error(
            f"Exception when calling CustomObjectsApi->patch_namespaced_custom_object: {e}"
        )

def create_pod(podname, jobname, imagename, namespace, commands):
        
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name=podname),
        spec=client.V1PodSpec(
            containers=[
                client.V1Container(
                    name=jobname,
                    image=imagename,
                    command=[ "/bin/sh", "-c", " && ".join(commands) ]
                )
            ],
             restart_policy="Never"
        )
    )
    
    api.create_namespaced_pod(namespace=namespace,body=pod)

def delete_pod(podname, namespace):
    api_response = api.delete_namespaced_pod(name, namespace)
    return api_response

@kopf.on.create(GROUP, VERSION, JOB_PLURAL)
def create_batchjob(spec, name, namespace, **kwargs):
    queue_name = spec.get('queueName')

    if not queue_name:
        logging.error(f"BatchJob {name} does not have a queueName specified.")
        return

    try:
        queue = api.get_namespaced_custom_object(
            group=GROUP, version=VERSION, namespace=namespace, plural=QUEUE_PLURAL, name=queue_name
        )
    except ApiException as e:
        logging.error(f"Queue {queue_name} not found: {e}")
        return

    if not queue:
        logging.error(f"Queue {queue_name} not found in namespace {namespace}.")
        return

    # Añadir el BatchJob a la lista de trabajos de la Queue
    queue_status = queue.get('status', {})
    queue_status['queuedJobs'] = queue_status.get('queuedJobs', 0) + 1
    queue_status['jobs'] = queue_status.get('jobs', []) + [name]
    update_queue_status(namespace, queue_name, {'status': queue_status})

    logging.info(f"BatchJob {name} added to queue {queue_name}")

@kopf.on.delete(GROUP, VERSION, JOB_PLURAL)
def delete_batchjob(spec, name, namespace, **kwargs):
    queue_name = spec.get('queueName')

    if not queue_name:
        logging.error(f"BatchJob {name} does not have a queueName specified.")
        return

    try:
        queue = api.get_namespaced_custom_object(
            group=GROUP, version=VERSION, namespace=namespace, plural=QUEUE_PLURAL, name=queue_name
        )
    except ApiException as e:
        logging.error(f"Queue {queue_name} not found: {e}")
        return

    if not queue:
        logging.error(f"Queue {queue_name} not found in namespace {namespace}.")
        return

    queue_status = queue.get('status', {})
    queue_status['jobs'] = [job for job in queue_status.get('jobs', []) if job != name]

    # Decrementar `queuedJobs` o `runningJobs` basado en si el trabajo estaba en ejecución o en cola
    if name in queue_status.get('jobs', []):
        # Si el trabajo estaba en cola, decrementa `queuedJobs`
        queue_status['queuedJobs'] = max(queue_status.get('queuedJobs', 1) - 1, 0)
    else:
        # Si el trabajo estaba en ejecución, decrementa `runningJobs`
        queue_status['runningJobs'] = max(queue_status.get('runningJobs', 1) - 1, 0)

    # api.delete_namespaced_pod(name, namespace)

    update_queue_status(namespace, queue_name, {'status': queue_status})
    
    logging.info(f"BatchJob {name} removed from queue {queue_name}")

@kopf.on.create(GROUP, VERSION, QUEUE_PLURAL)
def create_queue(spec, name, namespace, **kwargs):

    queue_status = {
        'runningJobs': 0,
        'queuedJobs': 0,
        'jobs': []
    }
    update_queue_status(namespace, name, {'status': queue_status})
    logging.info(f"Queue {name} created")

@kopf.timer(GROUP, VERSION, QUEUE_PLURAL, interval=60)
def prioritize_queues(spec, namespace, **kwargs):
    
    list_queues = get_list_queues(namespace)
    
    # Ordenamos las colas por prioridad
    order_list_queues = sorted(
        list_queues['items'], key=lambda k: k['spec'].get('priority', 0), reverse=True
    )

    for queue in order_list_queues:
        queue_status = queue.get('status', {})
        logging.info(f'Queue STATUS: {queue_status}')
        queue_name = queue['metadata']['name']
        while (
            (queue_status.get('queuedJobs', 0) > 0) and 
            (queue_status.get('runningJobs', 0) < queue.get('spec', {}).get('slots', 1))
        ):
            queue_status['queuedJobs'] -= 1
            queue_status['runningJobs'] += 1
            job_name = queue_status['jobs'].pop(0)

            batchjob = api.get_namespaced_custom_object(
                group=GROUP, version=VERSION, namespace=namespace, plural=JOB_PLURAL, 
                name=job_name
            )

            job_spec = batchjob['spec']['jobDetails']
            image = job_spec['image']
            commands = job_spec['commands']

            #create_pod(job_name, job_name, image, namespace, commands)
            
            # Actualizar el estado del trabajo (puedes iniciar el trabajo aquí)
            logging.info(f"Starting BatchJob {job_name} from queue {queue_name}")
            update_queue_status(namespace, queue_name, {'status': queue_status})

# Iniciar el operador
if __name__ == "__main__":
    kopf.run(namespaces=[NAMESPACE])
