from eventprocessor import BaseEventProcessor,register

class ControlFileArrivedEventProcessor(BaseEventProcessor):

	def __init__(self, invoker, event, data):
		'''
		we assume is_init_event was properly called and this event is acceptable
		:param invoker:
		:param event:
		:param data: This is assumed to be Server instance
		'''
		super(ControlFileArrivedEventProcessor, self).__init__(invoker, event, data)
		self.__fid = event['data']['folder']
		self.__file = event['data']['item']
		self.__client = 
		self.__server = data

	@classmethod
	def is_init_event(cls, event, data=None):
		'''
		:param event:
		:param data: This is assumed to be Server instance
		:return:
		'''
		clients = data.syncthingHandler.get_clients()
		folderids = [clients[x]['controlfolder']['id'] for x in clients]
		try:
			# TODO: do NOT trigger if file is a reply
			return event['type'] == 'ItemFinished' and event['data']['folder'] in folderids and event['data']['error'] is None and event['data']['action'] == 'update'
		except:
			return False

	def is_expected_event(self, event):
		return False

	def _processEvent(self, event):
		'''
		this should never be called cuz we do not expect any events after the first one
		'''
		raise NotImplementedError()

	def _runLoopLoad(self):
		'''
		read command file, execute command, send back results
		:return:
		'''
		clients = self.__data.syncthingHandler.get_clients()
		filepath = clients[]