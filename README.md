# kube2consul

based on kyarovoy's work.

# main purpose of this fork:

To register only exposed services in Consul (there is no access from outside to not exposed ones)

To support different Consul configurations

# parameters

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
  
 
  
 # To start a service in K8s edit kube2consul.yml.example to match your environment and load it into K8s

