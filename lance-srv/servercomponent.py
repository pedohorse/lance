import lance_utils

class ServerComponent(lance_utils.StoppableThread, lance_utils.EventQueueWriter):
	def __init__(self, server):
		super(ServerComponent, self).__init__()
		self.setEventQueue(server.eventQueue)
		self._server = server

	def config(self):
		return self._server.config
