"""kube2pyconsul.

Usage:
  kube2pyconsul.py [-v <loglevel>] [--verify-ssl] [--consul-agent=<consul-uri>] [--kube-master=<kubeapi-uri>]
  [--consul-auth=<user,pass>] [--kube-auth=<user,pass>] [--consul-token=token]
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

kubeapi_uri = args['--kube-master']
kube_auth = tuple(args['--kube-auth'].split(',')) if args['--kube-auth'] != 'None' else None

verify_ssl = args['--verify-ssl']

log.info("Starting with: consul={0}, kubeapi={1}".format(consul_uri, kubeapi_uri))


def getservice(event, ports):
    if event['object']['metadata']['name'] == "kubernetes":
        return "K8s"
    elif 'nodePort' in event['object']['spec']['ports'][0]:
        key, value = event['object']['spec']['selector'].items()[0]
        r = requests.get('{base}/api/v1/pods?labelSelector={key}={value}'.format(base=kubeapi_uri,
                                                                                 key=key, value=value),
                         auth=kube_auth, verify=verify_ssl)
        pod_json = json.loads(r.content)
        return {
                "ID": '{}-{}'.format(event['object']['metadata']['name'],
                                     event['object']['spec']['ports'][0]['nodePort']),
                "Name": event['object']['metadata']['name'],
                "Address": pod_json['items'][0]['status']['hostIP'],
                "Port": event['object']['spec']['ports'][0]['nodePort'],
                "Check": {
                          "TCP": "localhost:" + str(event['object']['spec']['ports'][0]['nodePort']),
                          "Interval": "10s"
                          }
                }
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


def registration(queue):
    while True:
        context, event = queue.get(block=True)
        if context == 'service':
            if event['type'] == 'ADDED':
                for ports in event['object']['spec']['ports']:
                    while True:
                        try:
                            service = getservice(event, ports)
                            break
                        except Exception as e:
                            log.debug(traceback.format_exc())
                            log.error(e)
                            log.error("Seems POD is not started yet. Sleeping and retrying.")
                            time.sleep(5)

                    if service != "K8s" and service != "NEXP":
                        r = ''
                        agent_base = consul_uri
                        while True:
                            try:
                                if consul_token:
                                    r = requests.post('{base}/v1/agent/service/register?token={token}'
                                                      .format(base=agent_base, token=consul_token),
                                                      json=service, auth=consul_auth, verify=verify_ssl,
                                                      allow_redirects=True)
                                else:
                                    r = requests.post('{base}/v1/agent/service/register'
                                                      .format(base=agent_base),
                                                      json=service, auth=consul_auth, verify=verify_ssl,
                                                      allow_redirects=True)
                                break
                            except Exception as e:
                                log.debug(traceback.format_exc())
                                log.error(e)
                                log.error("Sleeping and retrying.")
                                time.sleep(10)

                        if r.status_code == 200:
                            log.info("ADDED service {service} to Consul's catalog".format(service=service))
                        else:
                            log.error("Consul returned non-200 request status code. Could not register service "
                                      "{service}. Continuing on to the next service...".format(service=service))
                        sys.stdout.flush()
                    elif service == "K8s":
                        log.info("Skipping K8s service registration...")
                    else:
                        log.info("Service is not exposed. Skipping registration.")

            elif event['type'] == 'DELETED':
                for ports in event['object']['spec']['ports']:
                    service = getservice(event, ports)
                    r = ''
                    agent_base = consul_uri
                    while True:
                        try:
                            if consul_token:
                                r = requests.post('{base}/v1/agent/service/deregister/{id}?token={token}'
                                                  .format(base=agent_base, id=service['ID'],
                                                          port=str(service['Port']), token=consul_token),
                                                  auth=consul_auth, verify=verify_ssl)
                            else:
                                r = requests.post('{base}/v1/agent/service/deregister/{id}'
                                                  .format(base=agent_base, id=service['ID'],
                                                          port=str(service['Port'])),
                                                  auth=consul_auth, verify=verify_ssl)
                            break
                        except Exception as e:
                            log.debug(traceback.format_exc())
                            log.error(e)
                            log.error("Sleeping and retrying.")
                            time.sleep(10)
                            
                    if r.status_code == 200:
                        log.info("DELETED service {service} from Consul's catalog".format(service=service))
                    else:
                        log.error("Consul returned non-200 request status code. Could not deregister service {service}."
                                  " Continuing on to the next service...".format(service=service))
                    sys.stdout.flush()
                      
        elif context == 'pod':
            pass
        
        
def run():
    q = Queue()
    services_watch = Process(target=services_monitor, args=(q,), name='kube2pyconsul/services')
    pods_watch = Process(target=pods_monitor, args=(q,), name='kube2pyconsul/pods')
    consul_desk = Process(target=registration, args=(q,), name='kube2pyconsul/registration')
    
    services_watch.start()
    pods_watch.start()
    consul_desk.start()
    
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        services_watch.terminate()
        pods_watch.terminate()
        consul_desk.terminate()
        
        exit()

if __name__ == '__main__':
    run()
