"""kube2pyconsul.

Usage:
  kube2pyconsul.py [-v <loglevel>] [--verify-ssl] [--consul-agent=<consul-uri>]
  [--kube-master=<kubeapi-uri>] [--consul-auth=<user,pass>] [--kube-auth=<user,pass>]
  [--consul-token=token] [--TRAEFIK-kv-path=path]
  kube2pyconsul.py (-h | --help)

Options:
  -h --help     Show this screen.
  -v <loglevel>           Set logging level [default: INFO]
  --consul-agent=<consul-uri>  Consul agent location [default: http://127.0.0.1:8500].
  --kube-master=<kubeapi-uri>  Kubeapi location [default: https://127.0.0.1:443]
  --consul-auth=<user,pass>    Consul http auth credentials [default: None]
  --kube-auth=<user,pass>      Kubernetes http auth credentials [default: None]
  --verify-ssl	       	       Verify SSL or not when connecting to APIs [default: False]
  --consul-token=token         Token for ACL enabled consul
  --traefik-kv-path=path       root key of TRAEFIK config in Consul KV

"""
import sys
import json
import time
import logging
import traceback
import multiprocessing
from multiprocessing import Queue
from multiprocessing import Process
from docopt import docopt
import requests

requests.packages.urllib3.disable_warnings()

ARGS = docopt(__doc__, version='kube2pyconsul 1.0')

logging.basicConfig()
LOG = multiprocessing.log_to_stderr()
LEVEL = logging.getLevelName(ARGS['-v'])
LOG.setLevel(LEVEL)


CONSUL_URI = ARGS['--consul-agent']
CONSUL_AUTH = tuple(ARGS['--consul-auth'].split(',')) if ARGS['--consul-auth'] != 'None' else None
CONSUL_TOKEN = ARGS['--consul-token']
TRAEFIK = ARGS['--traefik-kv-path']

KUBEAPI_URI = ARGS['--kube-master']
KUBE_AUTH = tuple(ARGS['--kube-auth'].split(',')) if ARGS['--kube-auth'] != 'None' else None

VERIFY_SSL = ARGS['--verify-ssl']

LOG.info("Starting with: consul={0}, kubeapi={1}".format(CONSUL_URI, KUBEAPI_URI))


def get_kube_hosts():
    """Queries KubeAPI and returns workers as a list"""
    request_url = KUBEAPI_URI + '/api/v1/nodes'
    while True:
        try:
            ip_list = {}
            req = requests.get(request_url, verify=VERIFY_SSL, auth=KUBE_AUTH)
            nodes = json.loads(req.content)
            for index, hosts in enumerate(nodes['items']):
                ip_list[index] = hosts['status']['addresses'][0]['address']
            return ip_list
        except Exception as err:
            LOG.debug(traceback.format_exc())
            LOG.error(err)
            LOG.error("Error getting nodes from KubeAPI. restarting.")
            time.sleep(10)


def get_service(event):
    """Obsolete, but left for possible future functionality modifications
    takes event of 'service' context and returns json with urls
    """
    if event['object']['metadata']['name'] == "kubernetes":
        return "K8s"
    elif 'nodePort' in event['object']['spec']['ports'][0]:
        hosts = get_kube_hosts()
        resp = {}
        host = ''
        for index, host in enumerate(hosts):
            resp[hosts[index]] = 'http://' + str(hosts[index]) + ':' + \
                                 str(event['object']['spec']['ports'][0]['nodePort'])
        LOG.debug(host)
        return json.dumps(resp)
    else:
        return "NEXP"


def get_node_port(appname):
    """Gets nodePort for application appname. Returns int"""
    req = requests.get('{base}/api/v1/services?fieldSelector=metadata.name={value}'
                       .format(base=KUBEAPI_URI, value=appname))
    service_dict = json.loads(req.content)
    if len(service_dict['items']) > 0:
        try:
            node_port = service_dict['items'][0]['spec']['ports'][0]['nodePort']
        except Exception as err:
            LOG.debug(err.message)
            node_port = 0
    else:
        node_port = 0
    return node_port


def get_weight_label(appname):
    """Gets weight label from service. Returns int value or 1 if label was not found"""
    req = requests.get('{base}/api/v1/services?fieldSelector=metadata.name={value}'
                       .format(base=KUBEAPI_URI, value=appname))
    service_dict = json.loads(req.content)
    if len(service_dict['items']) > 0:
        try:
            weight = int(service_dict['items'][0]['metadata']['labels']['weight'])
            LOG.debug("Weight label found. Value: {value}".format(value=weight))
        except Exception as err:
            LOG.debug(err.message)
            weight = 1
    else:
        weight = 1
    return weight


def services_monitor(queue):
    """Obsolete, but left for possible future functionality modifications
    monitors service events as a subscriber and puts them into queue
    """
    while True:
        try:
            req = requests.get('{base}/api/v1/services?watch=true'.format(base=KUBEAPI_URI),
                               stream=True, verify=VERIFY_SSL, auth=KUBE_AUTH)
            for line in req.iter_lines():
                if line:
                    event = json.loads(line)
                    queue.put(('service', event))
        except Exception as err:
            LOG.debug(traceback.format_exc())
            LOG.error(err)
            LOG.error("Sleeping and restarting afresh.")
            time.sleep(10)


def pods_monitor(queue):
    """Obsolete, but left for possible future functionality modifications
    monitors pod events as a subscriber and puts them into queue
    """
    while True:
        try:
            req = requests.get('{base}/api/v1/pods?watch=true'.format(base=KUBEAPI_URI),
                               stream=True, verify=VERIFY_SSL, auth=KUBE_AUTH)
            for line in req.iter_lines():
                if line:
                    event = json.loads(line)
                    queue.put(('pod', event))
        except Exception as err:
            LOG.debug(traceback.format_exc())
            LOG.error(err)
            LOG.error("Sleeping and restarting afresh.")
            time.sleep(10)


def nodes_monitor(queue):
    """monitors node events and puts them into queue"""
    while True:
        try:
            req = requests.get('{base}/api/v1/nodes?watch=true'.format(base=KUBEAPI_URI),
                               stream=True, verify=VERIFY_SSL, auth=KUBE_AUTH)
            for line in req.iter_lines():
                if line:
                    event = json.loads(line)
                    queue.put(('node', event))
        except Exception as err:
            LOG.debug(traceback.format_exc())
            LOG.error(err)
            LOG.error("Sleeping and restarting afresh.")
            time.sleep(10)


def get_node_ip(event):
    """Gets node IP from node context event. Returns string"""
    return event['object']['status']['addresses'][0]['address']


def get_service_list():
    """Gets list of all services from KubeAPI. Returns json with strings like:
    service-development:0.22.2. Filters out only services containing
    router:traefik labels.
    """
    while True:
        try:
            req = requests.get('{base}/api/v1/services'
                               .format(base=KUBEAPI_URI), verify=VERIFY_SSL, auth=KUBE_AUTH)
            svs = json.loads(req.content)
            list_def = {}
            service = ''
            for idx, service in enumerate(svs['items']):
                if 'router' in svs['items'][idx]['metadata']['labels']:
                    if svs['items'][idx]['metadata']['labels']['router'] == 'traefik':
                        list_def[idx] = svs['items'][idx]['metadata']['labels']['service'] \
                                        + '-' \
                                        + svs['items'][idx]['metadata']['labels']['environment'] \
                                        + ':' \
                                        + svs['items'][idx]['metadata']['labels']['version']
            LOG.debug(service)
            return json.dumps(list_def)
        except Exception as err:
            LOG.debug(traceback.format_exc())
            LOG.error(err)
            LOG.error("Sleeping and restarting afresh.")
            time.sleep(10)


def url_beautify(url_string):
    """Removes additional quotes from URL string to put it to KV for traefik"""
    return url_string.replace('"', '')


def register_node(event):
    """Registers node for traefik for all services to make it act as backend"""
    req = None
    req.response = 0
    while True:
        try:
            node_ip = get_node_ip(event)
            svcs = json.loads(get_service_list())
            agent_base = CONSUL_URI
            for serv in svcs:
                port = get_node_port(svcs[serv])
                weight = get_weight_label(svcs[serv])
                if port != 0:
                    url = 'http://' + node_ip + ':' + str(port)
                    if CONSUL_TOKEN:
                        req = requests.put('{base}/v1/kv/{TRAEFIK}/backends/{app_name}/servers/'
                                           '{host}/url?token={token}'.format(base=agent_base,
                                                                             token=CONSUL_TOKEN,
                                                                             TRAEFIK=TRAEFIK,
                                                                             app_name=svcs[serv],
                                                                             host=node_ip),
                                           data=url_beautify(url),
                                           auth=CONSUL_AUTH, verify=VERIFY_SSL,
                                           allow_redirects=True)
                        req = requests.put('{base}/v1/kv/{TRAEFIK}/backends/{app_name}/servers/'
                                           '{host}/weight?token={token}'.format(base=agent_base,
                                                                                token=CONSUL_TOKEN,
                                                                                TRAEFIK=TRAEFIK,
                                                                                app_name=svcs[serv],
                                                                                host=node_ip),
                                           data=weight, auth=CONSUL_AUTH, verify=VERIFY_SSL,
                                           allow_redirects=True)
                    else:
                        req = requests.put('{base}/v1/kv/{TRAEFIK}/backends/{app_name}/servers/'
                                           '{host}/url'.format(base=agent_base,
                                                               TRAEFIK=TRAEFIK,
                                                               app_name=svcs[serv],
                                                               host=node_ip),
                                           data=url_beautify(url),
                                           auth=CONSUL_AUTH, verify=VERIFY_SSL,
                                           allow_redirects=True)
                        req = requests.put('{base}/v1/kv/{TRAEFIK}/backends/{app_name}/servers/'
                                           '{host}/weight'.format(base=agent_base,
                                                                  TRAEFIK=TRAEFIK,
                                                                  app_name=svcs[serv],
                                                                  host=node_ip),
                                           data=weight, auth=CONSUL_AUTH, verify=VERIFY_SSL,
                                           allow_redirects=True)
                    print "Service " + svcs[serv] + " on node " + node_ip + "registered."
                else:
                    print "Skipping " + svcs[serv] + " registration on node " + node_ip + \
                          ". nodePort not found."
            break

        except Exception as err:
            LOG.debug(traceback.format_exc())
            LOG.error(err.message)
            LOG.error("Sleeping and retrying.")
            time.sleep(10)

        if req.status_code == 200:
            LOG.info("ADDED service {service} to Consul's catalog".format(service=node_ip))
        else:
            LOG.error("Consul returned non-200 request status code. Could not register service "
                      "{service}. Continuing on to the next service...".format(service=node_ip))
        sys.stdout.flush()


def deregister_node(event):
    """Removes node from KV to exclude it from traefik backends"""
    req = None
    req.response = 0
    print "Deregistering node..."
    while True:
        try:
            node_ip = get_node_ip(event)
            svcs = json.loads(get_service_list())
            agent_base = CONSUL_URI
            for serv in svcs:
                if CONSUL_TOKEN:
                    req = requests.delete('{base}/v1/kv/{TRAEFIK}/backends/{app_name}/servers/'
                                          '{host}/url?token={token}'.format(base=agent_base,
                                                                            token=CONSUL_TOKEN,
                                                                            TRAEFIK=TRAEFIK,
                                                                            app_name=svcs[serv],
                                                                            host=node_ip),
                                          auth=CONSUL_AUTH, verify=VERIFY_SSL,
                                          allow_redirects=True)
                    req = requests.delete('{base}/v1/kv/{TRAEFIK}/backends/{app_name}/servers/'
                                          '{host}/weight?token={token}'.format(base=agent_base,
                                                                               token=CONSUL_TOKEN,
                                                                               TRAEFIK=TRAEFIK,
                                                                               app_name=svcs[serv],
                                                                               host=node_ip),
                                          auth=CONSUL_AUTH, verify=VERIFY_SSL,
                                          allow_redirects=True)
                    req = requests.delete('{base}/v1/kv/{TRAEFIK}/backends/{app_name}/servers/'
                                          '{host}?token={token}'.format(base=agent_base,
                                                                        token=CONSUL_TOKEN,
                                                                        TRAEFIK=TRAEFIK,
                                                                        app_name=svcs[serv],
                                                                        host=node_ip),
                                          auth=CONSUL_AUTH, verify=VERIFY_SSL,
                                          allow_redirects=True)
                else:
                    req = requests.delete('{base}/v1/kv/{TRAEFIK}/backends/{app_name}/servers/'
                                          '{host}/url'.format(base=agent_base,
                                                              TRAEFIK=TRAEFIK,
                                                              app_name=svcs[serv],
                                                              host=node_ip),
                                          auth=CONSUL_AUTH, verify=VERIFY_SSL,
                                          allow_redirects=True)
                    req = requests.delete('{base}/v1/kv/{TRAEFIK}/backends/{app_name}/servers/'
                                          '{host}/weight'.format(base=agent_base,
                                                                 TRAEFIK=TRAEFIK,
                                                                 app_name=svcs[serv],
                                                                 host=node_ip),
                                          auth=CONSUL_AUTH, verify=VERIFY_SSL,
                                          allow_redirects=True)
                    req = requests.delete('{base}/v1/kv/{TRAEFIK}/backends/{app_name}/servers/'
                                          '{host}'.format(base=agent_base,
                                                          TRAEFIK=TRAEFIK,
                                                          app_name=svcs[serv],
                                                          host=node_ip),
                                          auth=CONSUL_AUTH, verify=VERIFY_SSL,
                                          allow_redirects=True)
                print "Node {node} deregistered for service {service}"\
                    .format(node=node_ip, service=svcs[serv])
            break

        except Exception as err:
            LOG.debug(traceback.format_exc())
            LOG.error(err.message)
            LOG.error("Sleeping and retrying.")
            time.sleep(10)

        if req.status_code == 200:
            LOG.info("DEREGISTERED node {service} to Consul's catalog".format(service=node_ip))
        else:
            LOG.error("Consul returned non-200 request status code. Could not deregister node "
                      "{service}. Continuing on to the next node...".format(service=node_ip))
        sys.stdout.flush()


def registration(queue):
    """Monitors node events and triggers register/remove methods"""
    while True:
        context, event = queue.get(block=True)
        if context == 'node':
            if event['type'] == 'ADDED':
                register_node(event)

            elif event['type'] == 'DELETED':
                deregister_node(event)

            elif event['type'] == 'MODIFIED':
                if event['object']['status']['conditions'][2]['status'] == 'True':
                    register_node(event)
                elif event['object']['status']['conditions'][2]['status'] == 'False':
                    deregister_node(event)
                elif event['object']['status']['conditions'][2]['status'] == 'Unknown':
                    deregister_node(event)
        elif context == 'service':
            pass
        elif context == 'pod':
            pass


def run():
    """Main. Triggers all configured threads."""
    eventq = Queue()
    # services_watch = Process(target=services_monitor, args=(q,), name='kube2pyconsul/services')
    # pods_watch = Process(target=pods_monitor, args=(q,), name='kube2pyconsul/pods')
    nodes_watch = Process(target=nodes_monitor, args=(eventq,), name='kube2pyconsul/nodes')
    consul_desk = Process(target=registration, args=(eventq,), name='kube2pyconsul/registration')

    # services_watch.start()
    # pods_watch.start()
    nodes_watch.start()
    consul_desk.start()

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        # services_watch.terminate()
        # pods_watch.terminate()
        nodes_watch.terminate()
        consul_desk.terminate()

        exit()

if __name__ == '__main__':
    run()
