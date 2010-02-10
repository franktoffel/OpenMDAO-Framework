"""
Allocate servers from one or more resources (i.e. the local host, a cluster
of remote hosts, etc.)
"""

import logging
import os
import platform
import sys
import threading
import traceback

from openmdao.main import mp_distributing
from openmdao.main.objserverfactory import ObjServerFactory, ObjServer
from openmdao.util.eggloader import check_requirements


class ResourceAllocationManager(object):
    """
    The allocation manager maintains a list of allocators which are used
    to select the 'best fit' for a particular resource request.  The manager
    is initialized with a :class:`LocalAllocator` for the local host.
    """

    _lock = threading.Lock()
    _RAM = None  # Singleton.

    def __init__(self):
        self._logger = logging.getLogger('RAM')
        self._allocations = 0
        self._allocators = []
        self._alloc_index = 0
        self._allocators.append(LocalAllocator())

    @staticmethod
    def get_instance():
        """ Return singleton instance. """
        with ResourceAllocationManager._lock:
            if ResourceAllocationManager._RAM is None:
                ResourceAllocationManager._RAM = ResourceAllocationManager()
            return ResourceAllocationManager._RAM

    @staticmethod
    def add_allocator(allocator):
        """ Add an allocator to the list of resource allocators. """
        ram = ResourceAllocationManager.get_instance()
        with ResourceAllocationManager._lock:
            ram._allocators.append(allocator)

    @staticmethod
    def insert_allocator(index, allocator):
        """ Insert an allocator into the list of resource allocators. """
        ram = ResourceAllocationManager.get_instance()
        with ResourceAllocationManager._lock:
            ram._allocators.insert(index, allocator)

    @staticmethod
    def allocate(resource_desc):
        """
        Determine best resource for `resource_desc` and deploy.
        In the case of a tie, the first allocator in the allocators list wins.
        Returns (proxy-object, server-dict).
        """
        for handler in logging._handlerList:
            handler.flush()  # Try to keep log messages sane.

        ram = ResourceAllocationManager.get_instance()
        with ResourceAllocationManager._lock:
            return ram._allocate(resource_desc)

    def _allocate(self, resource_desc):
        """ Do the allocation. """
        deployment_retries = 0
        best_score = -1
        while best_score == -1:
            best_score, best_criteria, best_allocator = \
                self._get_scores(resource_desc)
            if best_score >= 0:
                self._allocations += 1
                name = 'Sim-%d' % self._allocations
                self._logger.debug('deploying on %s', best_allocator.name)
                server = best_allocator.deploy(name, resource_desc)
                if server is not None:
                    server_info = {
                        'name':server.get_name(),
                        'pid':server.get_pid(),
                        'host':server.get_host()
                    }
                    self._logger.debug('allocated %s pid %d on %s',
                                       server_info['name'], server_info['pid'],
                                       server_info['host'])
                    return (server, server_info)
                else:
                    deployment_retries += 1
                    if deployment_retries > 10:
                        self._logger.error('deployment failed too many times.')
                        return (None, None)
                    self._logger.warning('deployment failed, retrying.')
                    best_score = -1
            elif best_score != -1:
                return (None, None)
            else:
                time.sleep(1)

    def _get_scores(self, resource_desc):
        """ Return best (score, criteria, allocator). """
        best_score = -2
        best_criteria = None
        best_allocator = None

        for allocator in self._allocators:
            score, criteria = allocator.rate_resource(resource_desc)
            self._logger.debug('allocator %s returned %g',
                               allocator.name, score)
            if (best_score == -2 and score >= -1) or \
               (best_score == 0  and score >  0) or \
               (best_score >  0  and score < best_score):
                best_score = score
                best_criteria = criteria
                best_allocator = allocator

        return (best_score, best_criteria, best_allocator)

    @staticmethod
    def release(server):
        """ Release a server (proxy). """
        name = server.get_name()
        try:
            server.cleanup()
        except Exception:
            trace = traceback.format_exc()
            ram = ResourceAllocationManager.get_instance()
            try:
                ram._logger.warning('caught exception during cleanup of %s: %s',
                                    name, trace)
            except Exception:
                print >>sys.stderr, \
                      'RAM: caught exception logging cleanup of %s: %s', \
                      name, trace
        del server


class ResourceAllocator(ObjServerFactory):
    """ Estimates suitability of a resource and can deploy on that resource. """

    def __init__(self, name):
        super(ResourceAllocator, self).__init__()
        self.name = name
        self._logger = logging.getLogger(name)

    def rate_resource(self, resource_desc):
        """
        Return a score indicating how well this resource allocator can satisfy
        the `resource_desc` request.

        - >0 for an estimate of walltime (seconds).
        -  0 for no estimate.
        - -1 for no resource at this time.
        - -2 for no support for `resource_desc`.

        """
        raise NotImplementedError

    def check_required_distributions(self, resource_value):
        """
        Returns True if this allocator can support the specified required
        distributions.
        """
        required = []
        for dist in resource_value:
            required.append(dist.as_requirement())
        not_avail = check_requirements(sorted(required), logger=self._logger)
        if not_avail:  # Distribution not found or version conflict.
            return (-2, {'required_distributions' : not_avail})
        return (0, None)

    def check_orphan_modules(self, resource_value):
        """
        Returns True if this allocator can support the specified 'orphan'
        modules.
        """
#FIXME: shouldn't pollute the environment like this does.
        not_found = []
        for module in sorted(resource_value):
            self._logger.debug("checking for 'orphan' module: %s", module)
            try:
                __import__(module)
            except ImportError:
                self._logger.info('    not found')
                not_found.append(module)
        if len(not_found) > 0:  # Can't import module(s).
            return (-2, {'orphan_modules' : not_found})
        return (0, None)

    def deploy(self, resource_desc):
        """
        Deploy a server suitable for `resource_desc`.
        Returns a proxy to the deployed server.
        """
        raise NotImplementedError


class LocalAllocator(ResourceAllocator):
    """ Purely local resource allocator. """

    def __init__(self, total_cpus=1, max_load=2):
        super(LocalAllocator, self).__init__(name='LocalAllocator')
        self.total_cpus = total_cpus
        self.max_load = max_load

    def rate_resource(self, resource_desc):
        """
        Return a score indicating how well this resource allocator can satisfy
        the `resource_desc` request.

        - >0 for an estimate of walltime (seconds).
        -  0 for no estimate.
        - -1 for no resource at this time.
        - -2 for no support for `resource_desc`.

        """
        for key, value in resource_desc.items():
            if key == 'localhost':
                if not value:
                    return (-2, {key : value})  # Specifically not localhost.

            elif key == 'n_cpus':
                if value > self.total_cpus:
                    return (-2, {key : value})  # Too many cpus.

            elif key == 'required_distributions':
                score, info = self.check_required_distributions(value)
                if score < 0:
                    return (score, info)  # Not found or version conflict.

            elif key == 'orphan_modules':
                score, info = self.check_orphan_modules(value)
                if score < 0:
                    return (score, info)  # Can't import module(s).

            elif key == 'python_version':
                if sys.version[:3] != value:
                    return (-2, {key : value})  # Version mismatch.

            else:
                return (-2, {key : value})  # Unrecognized => unsupported.

        # Check system load.
        try:
            loadavgs = os.getloadavg()
        except AttributeError:
            return (0, {})
        self._logger.debug('loadavgs %.2f, %.2f, %.2f, max_load %d',
                           loadavgs[0], loadavgs[1], loadavgs[2], self.max_load)
        if loadavgs[0] < self.max_load:
            return (0, {'loadavgs' : loadavgs, 'max_load' : self.max_load})
        else:
            return (-1, {'loadavgs' : loadavgs, 'max_load' : self.max_load})

    def deploy(self, name, resource_desc):
        """
        Deploy a server suitable for `resource_desc`.
        Returns a proxy to the deployed server.
        """
        return self.create(typname='', name=name)

    @staticmethod
    def register(manager):
        """ Register :class:`LocalAllocator` proxy info with `manager`. """
        name = 'LocalAllocator'
        ObjServer.register(manager)
        method_to_typeid = {
            'deploy': 'ObjServer',
        }
        manager.register(name, LocalAllocator,
                         method_to_typeid=method_to_typeid)

LocalAllocator.register(mp_distributing.Cluster)
LocalAllocator.register(mp_distributing.HostManager)


class ClusterAllocator(object):
    """
    Cluster-based resource allocator.  This allocator manages a collection
    of :class:`LocalAllocator`, one for each machine in the cluster.
    `machines` is a list of dictionaries providing configuration data for each
    machine in the cluster.  At a minimum, each dictionary must specify a host
    address in 'hostname' and the path to the OpenMDAO python command in
    'python'.
    """

    def __init__(self, name, machines):
        self.name = name
        self._logger = logging.getLogger(name)
        self._lock = threading.Lock()
        self.machines = machines
        self.local_allocators = {}

        hosts = []
        for machine in machines:
            self._logger.debug('initializing %s', machine)
            host = mp_distributing.Host(machine['hostname'], slots=1,
                                        python=machine['python'])
            LocalAllocator.register(host)
            hosts.append(host)

        self.cluster = mp_distributing.Cluster(hosts, [])
        self.cluster.start()
        self._logger.debug('server listening on %s', self.cluster.address)

    def rate_resource(self, resource_desc):
        """
        Return a score indicating how well this resource allocator can satisfy
        the `resource_desc` request.

        - >0 for an estimate of walltime (seconds).
        -  0 for no estimate.
        - -1 for no resource at this time.
        - -2 for no support for `resource_desc`.

        """
        return (0, None)

    def deploy(self, name, resource_desc):
        """
        Deploy a server suitable for `resource_desc`.
        Returns a proxy to the deployed server.
        """
        manager = self.cluster.get_host_manager()
        try:
            host = manager._name
        except AttributeError:
            host = 'localhost'
            host_ip = '127.0.0.1'
        else:
            # 'host' is 'Host-<ipaddr>:<port>
            dash = host.index('-')
            colon = host.index(':')
            host_ip = host[dash+1:colon]

        with self._lock:
            if host_ip not in self.local_allocators:
                self.local_allocators[host_ip] = manager.LocalAllocator()
                self._logger.debug('LocalAllocator for %s %s', host,
                                   self.local_allocators[host_ip])

        return self.local_allocators[host_ip].deploy(name, resource_desc)
