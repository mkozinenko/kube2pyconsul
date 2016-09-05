"""kube2pyconsul.

Usage:
  kube2pyconsul.py [-v <loglevel>] [--verify-ssl] [--consul-agent=<consul-uri>] [--kube-master=<kubeapi-uri>]
  [--consul-auth=<user,pass>] [--kube-auth=<user,pass>] [--consul-token=token] [--traefik-kv-path=path]
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
  --traefik-kv-path=path       root key of traefik config in Consul KV

"""
from docopt import docopt

import sys
import json
import time
import logging
import requests
import traceback
import multiprocessing

from multiprocessing import Queue
from multiprocessing import Process

requests.packages.urllib3.disable_warnings()

args = docopt(__doc__, version='kube2pyconsul 1.0')

logging.basicConfig()
log = multiprocessing.log_to_stderr()
level = logging.getLevelName(args['-v'])
log.setLevel(level)


consul_uri = args['--consul-agent']
consul_auth = tuple(args['--consul-auth'].split(',')) if args['--consul-auth'] != 'None' else None
consul_token = args['--consul-token']
traefik_path = args['--traefik-kv-path']

kubeapi_uri = args['--kube-master']
kube_auth = tuple(args['--kube-auth'].split(',')) if args['--kube-auth'] != 'None' else None

verify_ssl = args['--verify-ssl']

log.info("Starting with: consul={0}, kubeapi={1}".format(consul_uri, kubeapi_uri))


def get_kube_hosts():
    request_url = kubeapi_uri + '/api/v1/nodes'
    while True:
        try:
            ip_list = {}
            r = requests.get(request_url, verify=verify_ssl, auth=kube_auth)
            nodes = json.loads(r.content)
            for index, hosts in enumerate(nodes['items']):
                ip_list[index] = hosts['status']['addresses'][0]['address']
            return ip_list
        except Exception as e:
            log.debug(traceback.format_exc())
            log.error(e)
            log.error("Error getting nodes from KubeAPI. restarting.")
            time.sleep(10)


def get_service(event):
    if event['object']['metadata']['name'] == "kubernetes":
        return "K8s"
    elif 'nodePort' in event['object']['spec']['ports'][0]:
        hosts = get_kube_hosts()
        resp = {}
        for index, host in enumerate(hosts):
            resp[hosts[index]] = 'http://' + str(hosts[index]) + ':' + \
                                 str(event['object']['spec']['ports'][0]['nodePort'])
        return json.dumps(resp)
    else:
        return "NEXP"


def get_node_port(appname):
    r = requests.get('{base}/api/v1/services?fieldSelector=metadata.name={value}'.format(base=kubeapi_uri,
                                                                                         value=appname))
    service_dict = json.loads(r.content)
    if len(service_dict['items']) > 0:
        try:
            node_port = service_dict['items'][0]['spec']['ports'][0]['nodePort']
        except Exception as e:
            log.debug(e.message)
            print "nodePort not found for ", service_dict['items'][0]['metadata']['name']
            node_port = 0
    else:
        node_port = 0
    return node_port


def get_weight_label(event):
    if event['object']['metadata']['name'] == "kubernetes":
        return "K8s"
    elif 'nodePort' in event['object']['spec']['ports'][0]:
        key, value = event['object']['spec']['selector'].items()[0]
        r = requests.get('{base}/api/v1/pods?labelSelector={key}={value}'.format(base=kubeapi_uri,
                                                                                 key=key, value=value),
                         auth=kube_auth, verify=verify_ssl)
        pod_dict = json.loads(r.content)
        resp = int(pod_dict['items'][0]['metadata']['labels']['weight'])
        return resp
    else:
        return "NEXP"


def services_monitor(queue):
    while True:
        try:
            r = requests.get('{base}/api/v1/services?watch=true'.format(base=kubeapi_uri), 
                             stream=True, verify=verify_ssl, auth=kube_auth)
            for line in r.iter_lines():
                if line:
                    event = json.loads(line)
                    queue.put(('service', event))
        except Exception as e:
            log.debug(traceback.format_exc())
            log.error(e)
            log.error("Sleeping and restarting afresh.")
            time.sleep(10)

    
def pods_monitor(queue):
    while True:
        try:
            r = requests.get('{base}/api/v1/pods?watch=true'.format(base=kubeapi_uri),
                             stream=True, verify=verify_ssl, auth=kube_auth)
            for line in r.iter_lines():
                if line:
                    event = json.loads(line)
                    queue.put(('pod', event))
        except Exception as e:
            log.debug(traceback.format_exc())
            log.error(e)
            log.error("Sleeping and restarting afresh.")
            time.sleep(10)


def nodes_monitor(queue):
    while True:
        try:
            r = requests.get('{base}/api/v1/nodes?watch=true'.format(base=kubeapi_uri),
                             stream=True, verify=verify_ssl, auth=kube_auth)
            for line in r.iter_lines():
                if line:
                    event = json.loads(line)
                    queue.put(('node', event))
        except Exception as e:
            log.debug(traceback.format_exc())
            log.error(e)
            log.error("Sleeping and restarting afresh.")
            time.sleep(10)


def get_node_ip(event):
    return event['object']['status']['addresses'][0]['address']


def get_service_list():
    while True:
        try:
            r = requests.get('{base}/api/v1/services'.format(base=kubeapi_uri), verify=verify_ssl, auth=kube_auth)
            services_dict = json.loads(r.content)
            list_def = {}
            for index, service in enumerate(services_dict['items']):
                list_def[index] = services_dict['items'][index]['metadata']['name']
            return json.dumps(list_def)
        except Exception as e:
            log.debug(traceback.format_exc())
            log.error(e)
            log.error("Sleeping and restarting afresh.")
            time.sleep(10)


def register_node(event):
    r = ''
    while True:
        try:
            node_ip = get_node_ip(event)
            services = json.loads(get_service_list())
            agent_base = consul_uri
            for service in services:
                port = get_node_port(services[service])
                if port != 0:
                    url = 'http://' + node_ip + ':' + str(port)
                    if consul_token:
                        r = requests.put('{base}/v1/kv/{traefik}/backends/{app_name}/servers/{host}/url?token='
                                         '{token}'.format(base=agent_base, token=consul_token,
                                                          traefik=traefik_path, app_name=services[service],
                                                          host=node_ip),
                                         json=url, auth=consul_auth, verify=verify_ssl,
                                         allow_redirects=True)
                    else:
                        r = requests.put('{base}/v1/kv/{traefik}/backends/{app_name}/servers/{host}/url'
                                         .format(base=agent_base, traefik=traefik_path, app_name=services[service],
                                                 host=node_ip),
                                         json=url, auth=consul_auth, verify=verify_ssl,
                                         allow_redirects=True)
                    print "Service " + services[service] + " on node " + node_ip + "registered."
                else:
                    print "Skipping " + services[service] + " registration on node " + node_ip + ". nodePort not found."
            break

        except Exception as e:
            log.debug(traceback.format_exc())
            log.error(e.message)
            log.error("Sleeping and retrying.")
            time.sleep(10)

        if r.status_code == 200:
            log.info("ADDED service {service} to Consul's catalog".format(service=node_ip))
        else:
            log.error("Consul returned non-200 request status code. Could not register service "
                      "{service}. Continuing on to the next service...".format(service=node_ip))
        sys.stdout.flush()


def deregister_node(event):
    r = ''
    print "Deregistering node..."
    while True:
        try:
            node_ip = get_node_ip(event)
            services = json.loads(get_service_list())
            agent_base = consul_uri
            for service in services:
                if consul_token:
                    r = requests.delete('{base}/v1/kv/{traefik}/backends/{app_name}/servers/{host}/url?token='
                                        '{token}'.format(base=agent_base, token=consul_token,
                                                         traefik=traefik_path, app_name=services[service],
                                                         host=node_ip),
                                        auth=consul_auth, verify=verify_ssl,
                                        allow_redirects=True)
                    r = requests.delete('{base}/v1/kv/{traefik}/backends/{app_name}/servers/{host}/weight?token='
                                        '{token}'.format(base=agent_base, token=consul_token,
                                                         traefik=traefik_path, app_name=services[service],
                                                         host=node_ip),
                                        auth=consul_auth, verify=verify_ssl,
                                        allow_redirects=True)
                    r = requests.delete('{base}/v1/kv/{traefik}/backends/{app_name}/servers/{host}?token='
                                        '{token}'.format(base=agent_base, token=consul_token,
                                                         traefik=traefik_path, app_name=services[service],
                                                         host=node_ip),
                                        auth=consul_auth, verify=verify_ssl,
                                        allow_redirects=True)
                else:
                    r = requests.delete('{base}/v1/kv/{traefik}/backends/{app_name}/servers/{host}/url'
                                        .format(base=agent_base, traefik=traefik_path, app_name=services[service],
                                                host=node_ip),
                                        auth=consul_auth, verify=verify_ssl,
                                        allow_redirects=True)
                    r = requests.delete('{base}/v1/kv/{traefik}/backends/{app_name}/servers/{host}/weight'
                                        .format(base=agent_base, traefik=traefik_path, app_name=services[service],
                                                host=node_ip),
                                        auth=consul_auth, verify=verify_ssl,
                                        allow_redirects=True)
                    r = requests.delete('{base}/v1/kv/{traefik}/backends/{app_name}/servers/{host}'
                                        .format(base=agent_base, traefik=traefik_path, app_name=services[service],
                                                host=node_ip),
                                        auth=consul_auth, verify=verify_ssl,
                                        allow_redirects=True)
                print "Node {node} deregistered for service {service}".format(node=node_ip, service=services[service])
            break

        except Exception as e:
            log.debug(traceback.format_exc())
            log.error(e.message)
            log.error("Sleeping and retrying.")
            time.sleep(10)

        if r.status_code == 200:
            log.info("DEREGISTERED node {service} to Consul's catalog".format(service=node_ip))
        else:
            log.error("Consul returned non-200 request status code. Could not deregister node "
                      "{service}. Continuing on to the next node...".format(service=node_ip))
        sys.stdout.flush()


def registration(queue):
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
                      
        elif context == 'pod':
            pass
        
        
def run():
    q = Queue()
    # services_watch = Process(target=services_monitor, args=(q,), name='kube2pyconsul/services')
    # pods_watch = Process(target=pods_monitor, args=(q,), name='kube2pyconsul/pods')
    nodes_watch = Process(target=nodes_monitor, args=(q,), name='kube2pyconsul/nodes')
    consul_desk = Process(target=registration, args=(q,), name='kube2pyconsul/registration')
    
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
