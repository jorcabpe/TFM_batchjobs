import logging

import kopf
import kubernetes
import kubernetes.config
from kubernetes import client
from kubernetes.client import ApiException, CustomObjectsApi
import asyncio

# Definir un lock
event_lock = asyncio.Lock()

# Definir las constantes
GROUP = "batch.upv.es"
VERSION = "v1"
QUEUE_PLURAL = "queues"
JOB_PLURAL = "batchjobs"
NAMESPACE = 'default'

logging.basicConfig(format='[%(asctime)s] - %(message)s', level=logging.INFO)

# Configura el logger de Kopf para desactivar los logs de nivel INFO
kopf_logger = logging.getLogger('kopf.objects')
kopf_logger.setLevel(logging.WARNING)  # Muestra solo WARNING o más alto, omite INFO y DEBUG

kubernetes.config.load_kube_config()
api = CustomObjectsApi()


def get_list_queues():
    try:
        return api.list_namespaced_custom_object(
            group=GROUP, version=VERSION, namespace=NAMESPACE,
            plural=QUEUE_PLURAL
        )
    except ApiException as e:
        logging.error(
            f"""Exception when calling
            CustomObjectsApi->list_namespaced_custom_object: {e}
            """
        )
        return []


def update_queue_status(queue_name, queue_status):

    try:
        api.patch_namespaced_custom_object(
            group=GROUP, version=VERSION, namespace=NAMESPACE,
            plural=QUEUE_PLURAL, name=queue_name, body=queue_status
        )
    except ApiException as e:
        logging.error(
            f"""Exception when calling
            CustomObjectsApi->patch_namespaced_custom_object: {e}
            """
        )


def create_pod(job_name, queue_name, image_name, commands, resources):
    labels = {
        "batchjob": job_name,
        "queue": queue_name
    }

    v1 = client.CoreV1Api()
    pod_name = f'pod{job_name}'
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name=pod_name, labels=labels),
        spec=client.V1PodSpec(
            containers=[
                client.V1Container(
                    name=job_name,
                    image=image_name,
                    command=["/bin/sh", "-c", " && ".join(commands)],
                    resources=client.V1ResourceRequirements(
                        requests=resources.get('requests', {}),
                        limits=resources.get('limits', {})
                    )
                )
            ],
            restart_policy="Never"
        )
    )

    v1.create_namespaced_pod(namespace=NAMESPACE, body=pod)


def prioritize_batchjobs_of_a_queue(queue):
    queue_name = queue['metadata']['name']
    try:
        list_batchjobs_in_queues = api.list_namespaced_custom_object(
            group=GROUP, version=VERSION, namespace=NAMESPACE,
            plural=JOB_PLURAL
        )
    except ApiException as e:
        logging.error(
            f"""Exception when calling
            CustomObjectsApi->list_namespaced_custom_object: {e}
            """
        )

    ordered_list_queues = sorted(
        list_batchjobs_in_queues['items'],
        key=lambda k: k['spec'].get('priority', 0), reverse=True
    )

    queue_status = queue.get('status', {})
    queue_status['queuedJobs'] = [
        job['metadata']['name']
        for job in ordered_list_queues
        if job['metadata']['name']
        not in queue_status['runningJobs']
    ]

    update_queue_status(queue_name, {'status': queue_status})


def get_custom_object(object_plural, object_name):
    try:
        return api.get_namespaced_custom_object(
            group=GROUP, version=VERSION, namespace=NAMESPACE,
            plural=object_plural, name=object_name
        )
    except ApiException as e:
        logging.error(
            f"""Exception when calling
            CustomObjectsApi->get_namespaced_custom_object: {e}
            """
        )
        return


@kopf.timer(VERSION, 'pods', interval=1)
async def monitor_pod_status(name, labels, status, **kwargs):
    async with event_lock:
        phase = status.get('phase')

        if phase == 'Succeeded':
            queue_name = labels['queue']
            job_name = labels['batchjob']
            queue = get_custom_object(QUEUE_PLURAL, queue_name)

            queue_status = queue.get('status', {})
            queue_status['runningJobs'] = [
                job
                for job in queue_status.get('runningJobs', [])
                if job != job_name and
                job not in queue_status['queuedJobs']
            ]

            update_queue_status(queue_name, {'status': queue_status})

            v1 = client.CoreV1Api()
            try:
                v1.delete_namespaced_pod(name=name, namespace=NAMESPACE)
            except ApiException as e:
                logging.error(
                    f"""{name}: Exception when calling
                    v1->delete_namespaced_pod: {e}
                    """
                )
                return

            try:
                api.delete_namespaced_custom_object(
                    group=GROUP,
                    version=VERSION,
                    namespace=NAMESPACE,
                    plural=JOB_PLURAL,
                    name=job_name,
                    body={},
                )
            except ApiException as e:
                logging.error(
                    f"""Exception when calling
                    CustomObjectsApi->delete_namespaced_custom_object: {e}
                    """
                )
                return

            logging.info(f"[MONITORING][REMOVING] - Removing Pod: {name}")
            # logging.info(f"[REMOVING] - Removing Job: {job_name}\n")
            #logging.info(f"queuedJobs: {queue_status['queuedJobs']}")
            #logging.info(f"runningJobs: {queue_status['runningJobs']}")


@kopf.on.create(GROUP, VERSION, JOB_PLURAL)
async def create_batchjob(spec, name, namespace, **kwargs):
    async with event_lock:
        queue_name = spec.get('queueName')

        if not queue_name:
            logging.error(
                f"BatchJob {name} does not have a queueName specified."
            )
            return
        queue = get_custom_object(QUEUE_PLURAL, queue_name)

        if not queue:
            logging.error(
                f"""Queue {queue_name} not found in
                namespace {namespace}."""
            )
            return

        queue_status = queue.get('status', {})
        queue_status['queuedJobs'] = queue_status.get(
            'queuedJobs', []
        ) + [name]
        update_queue_status(queue_name, {'status': queue_status})

        logging.info(f"[ADDING JOB TO QUEUE] - BatchJob {name} added to queue {queue_name}\n")
        #logging.info(f"queuedJobs: {queue_status['queuedJobs']}")
        #logging.info(f"runningJobs: {queue_status['runningJobs']}")


@kopf.on.delete(GROUP, VERSION, JOB_PLURAL)
async def delete_batchjob(spec, name, namespace, **kwargs):
    async with event_lock:
        queue_name = spec.get('queueName')

        queue = get_custom_object(QUEUE_PLURAL, queue_name)

        queue_status = queue.get('status', {})
        queue_status['queuedJobs'] = [
            job
            for job in queue_status.get('queuedJobs', [])
            if job != name and
            job not in queue_status['runningJobs']
        ]

        update_queue_status(queue_name, {'status': queue_status})

        try:
            api.delete_namespaced_custom_object(
                group=GROUP,
                version=VERSION,
                namespace=NAMESPACE,
                plural=JOB_PLURAL,
                name=name,
                body={},
            )
        except ApiException as e:
            logging.error(
                f"""Exception when calling
                CustomObjectsApi->delete_namespaced_custom_object: {e}
                """
            )
            return

        logging.info(f"[MONITORING][REMOVING] - BatchJob {name} removed from queue {queue_name}\n")


@kopf.on.create(GROUP, VERSION, QUEUE_PLURAL)
async def create_queue(spec, name, namespace, **kwargs):
    async with event_lock:
        queue_status = {
            'runningJobs': [],
            'queuedJobs': []
        }
        update_queue_status(name, {'status': queue_status})
        logging.info(f"Queue {name} created\n")


@kopf.on.delete(GROUP, VERSION, QUEUE_PLURAL)
async def delete_queue(name, namespace, **kwargs):
    async with event_lock:
        try:
            api.delete_namespaced_custom_object(
                group=GROUP,
                version=VERSION,
                namespace=NAMESPACE,
                plural=QUEUE_PLURAL,
                name=name,
                body={},
            )
        except ApiException as e:
            logging.error(
                f"""Exception when calling
                CustomObjectsApi->delete_namespaced_custom_object: {e}
                """
            )
            return

        logging.info(f"Queue {name} removed\n")


@kopf.timer(GROUP, VERSION, QUEUE_PLURAL, interval=30)
async def scheduling(spec, namespace, **kwargs):
    async with event_lock:
        list_queues = get_list_queues()

        order_list_queues = sorted(
            list_queues['items'],
            key=lambda k: k['spec'].get('priority', 0), reverse=True
        )

        for queue in order_list_queues:
            queue_status = queue.get('status', {})
            queue_name = queue['metadata']['name']
            prioritize_batchjobs_of_a_queue(queue)
            while (
                (len(queue_status.get('queuedJobs', [])) > 0) and
                (
                    len(queue_status.get('runningJobs', [])) <
                    queue.get('spec', {}).get('slots', 1)
                )
            ):
                job_name = queue_status['queuedJobs'].pop(0)
                queue_status['runningJobs'] += [job_name]
                queue_status['queuedJobs'] = [
                    job
                    for job in queue_status.get('queuedJobs', [])
                    if job != job_name and
                    job not in queue_status['runningJobs']
                ]

                batchjob = get_custom_object(JOB_PLURAL, job_name)

                job_spec = batchjob['spec']['jobDetails']
                image = job_spec['image']
                commands = job_spec['commands']
                resources = job_spec['resources']

                create_pod(job_name, queue_name, image, commands, resources)

                update_queue_status(queue_name, {'status': queue_status})

                logging.info(
                    f"""[SCHEDULING] - Starting BatchJob {job_name} from queue {queue_name}\n"""
                )
                #logging.info(f"queuedJobs: {queue_status['queuedJobs']}")
                #logging.info(f"runningJobs: {queue_status['runningJobs']}")


if __name__ == "__main__":
    kopf.run(namespaces=[NAMESPACE])
