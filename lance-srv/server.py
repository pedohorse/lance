from __future__ import print_function

import os
import Queue
import xml.etree.ElementTree as ET
from xml.dom import minidom
import lance_utils

from syncthinghandler import SyncthingHandler
from eventqueueeater import EventQueueEater
from databasehandler import DatabaseHandler

class Server(object):
	'''
	The point of this class is to serve as a container for all the internals of the server.
	To connect and kickstart the routine
	'''
	def __init__(self):
		super(Server, self).__init__()
		#self.data_root = os.path.join(os.path.split(os.path.abspath(__file__))[0], r'data')
		#self.config_root = os.path.join(os.path.split(os.path.abspath(__file__))[0], r'config')
		self.config = {'config_root': os.path.join(os.path.split(os.path.abspath(__file__))[0], r'config'), 'data_root': os.path.join(os.path.split(os.path.abspath(__file__))[0], r'data')}


		lance_utils.makedirs(self.config['data_root'])
		lance_utils.makedirs(self.config['config_root'])

		self.eventQueue = Queue.Queue()
		self.syncthingHandler = SyncthingHandler(self)
		self.eventQueueEater = EventQueueEater(self)
		self.databaseHandler = DatabaseHandler(self, os.path.join(self.config['data_root'], 'database.sqlite3'))

		self.eventQueueEater.set_default_event_data(self)  # set default data passed to new events to this server

		self.databaseHandler.start()
		self.syncthingHandler.start()
		conf = self.databaseHandler.get_configuration().result()
		self.syncthingHandler.apply_configuration(conf)


if __name__ == '__main__':
	srv = Server()