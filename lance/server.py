import os
import queue
from . import lance_utils

from .syncthinghandler import SyncthingHandler
from .eventqueueeater import EventQueueEater


class Server(object):
	"""
	The point of this class is to serve as a container for all the internals of the server.
	To connect and kickstart the routine
	"""
	def __init__(self, config_root_path=None, data_root_path=None):
		super(Server, self).__init__()

		if config_root_path is None:
			config_root_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), r'config')

		if data_root_path is None:
			data_root_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), r'data')

		self.config = {'config_root': config_root_path, 'data_root': data_root_path}

		lance_utils.makedirs(self.config['data_root'])
		lance_utils.makedirs(self.config['config_root'])

		self.eventQueue = queue.Queue()
		self.syncthingHandler = SyncthingHandler(self)
		self.eventQueueEater = EventQueueEater(self)

	def start(self):
		self.eventQueueEater.start()
		self.syncthingHandler.start()


	def stop(self):
		self.syncthingHandler.stop()
		self.eventQueueEater.stop()

if __name__ == '__main__':
	srv = Server()