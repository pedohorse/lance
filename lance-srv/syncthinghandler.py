import os
from servercomponent import ServerComponent
import lance_utils
from lance_utils import async
import subprocess
import urllib2
import json
import time
import xml.etree.ElementTree as ET
from xml.dom import minidom

class SyncthingNotReadyError(RuntimeError):
	pass

class SyncthingHandler(ServerComponent):
	def __init__(self, server):
		super(SyncthingHandler, self).__init__(server)

		self.syncthing_bin = r'D:\bin\syncthing\syncthing.exe'
		self.data_root = server.config['data_root'] #os.path.join(os.path.split(os.path.abspath(__file__))[0], r'data')
		self.config_root = server.config['config_root'] #os.path.join(os.path.split(os.path.abspath(__file__))[0], r'config')

		self.syncthing_ip = '127.0.0.1'
		self.syncthing_port = 9394
		self.syncthing_proc = None

		self._last_event_id = 0

		self._generate_initial_config()
		tree = ET.parse(os.path.join(self.config_root, 'config.xml'))
		self.httpheaders = {'X-API-Key': tree.find('gui').find('apikey').text, 'Content-Type': 'application/json'}

	def run(self):
		'''
		main loop
		:return:
		'''
		while not self._stopped_set():
			self._processAsyncMethods(time_to_wait=5)
			if self.syncthing_proc is not None:
				events = self.__get('/rest/events', since=self._last_event_id, timeout=1)
				for event in events:
					self._enqueueEvent(event)
					if self._last_event_id < event['id']:
						self._last_event_id = event['id']
			time.sleep(1)

	@async
	def apply_configuration(self, cfg):
		print('applying config %s', repr(cfg))
		conf = ET.Element('configuration', {'version': '28'})

		devices = cfg['devices']
		blacklist = cfg.get('blacklist', [])
		folders = cfg.get('folders', [])

		for dev in devices:
			dvc = ET.SubElement(conf, 'device', {'id': dev, 'compression': devices[dev].get('compression', 'metadata'), 'introducer': 'false'})
			ET.SubElement(dvc, 'address').text = devices[dev]['address']
			#now create control folder
		for dev in blacklist:
			ET.SubElement(conf, 'ignoredDevice').text = dev

		for fol in folders:
			foldc = ET.SubElement(conf, 'folder', {'id': fol, 'label': folders[fol]['label'], 'path': folders[fol]['path'], 'type': folders[fol].get('type', 'sendreceive')})
			for dev in folders[fol]['devices']:
				ET.SubElement(foldc, 'device', {'id': dev})

		st_conffile = os.path.join(self.config_root, 'config.xml')
		conf_old = ET.parse(st_conffile).getroot()
		guielem = conf_old.find('gui')
		optelem = conf_old.find('options')
		guielem.text = None
		guielem.tail = None
		optelem.text = None
		optelem.tail = None
		for chld in guielem:
			chld.tail = None
		for chld in optelem:
			chld.tail = None
		conf.append(guielem)
		conf.append(optelem)

		#ET.tostring(conf, encoding='UTF-8') #
		conftext = minidom.parseString(ET.tostring(conf, encoding='UTF-8')).toprettyxml()
		if self.syncthing_proc is None:
			with open(st_conffile, 'w') as f:
				f.write(conftext)
		else:
			self.post('/rest/system/config', conftext)
			self.post('/rest/system/restart', '')

	@async
	def start_syncthing(self):
		if not self.syncthing_proc or self.syncthing_proc.poll() is not None:
			self.synctiong_proc = subprocess.Popen([self.syncthing_bin, '-home={home}'.format(home=self.config_root), '-no-console', '-no-browser', '-no-restart', '-gui-address={addr}:{port}'.format(addr=self.syncthing_ip, port=self.syncthing_port) ])
			return True
		return False

	@async
	def stop_syncthing(self):
		if self.synctiong_proc:
			self.synctiong_proc.terminate()
			self.synctiong_proc.wait()
			self.synctiong_proc = None
			return True
		return False

	def __get(self, path, **kwargs):
		if self.syncthing_proc is None:
			raise SyncthingNotReadyError()
		url = "http://%s:%d%s" % (self.syncthing_ip, self.syncthing_port, path)
		if len(kwargs) > 0:
			url += '?' + '&'.join(['%s=%s' % (k, str(kwargs[k])) for k in kwargs.keys()])
		req = urllib2.Request(url, headers=self.httpheaders)
		try:
			rep = urllib2.urlopen(req)
		except urllib2.URLError as e:
			print("syncthing ui unresponsive, stopping process")
			print(e)
			self.stop_syncthing()
			return
		data = json.loads(rep.read())
		return data

	def __post(self, path, data):
		if self.syncthing_proc is None:
			raise SyncthingNotReadyError()
		req = urllib2.Request("http://%s:%d%s" % (self.syncthing_ip, self.syncthing_port, path), data, headers=self.httpheaders)
		try:
			rep = urllib2.urlopen(req)
		except urllib2.URLError as e:
			print("syncthing ui unresponsive, stopping process")
			print(e)
			self.stop_syncthing()
			return

	@async
	def get(self, path, **kwargs):
		return self.__get(path, **kwargs)

	@async
	def post(self, path, data):
		return self.__post(path, data)

	def _generate_initial_config(self):
		if self.syncthing_proc:
			raise SyncthingNotReadyError('Syncthing must not be running!')

		proc = subprocess.Popen([self.syncthing_bin, '-home={home}'.format(home=self.config_root), '-no-console', '-no-browser', '-no-restart', '-gui-address={addr}:{port}'.format(addr=self.syncthing_ip, port=self.syncthing_port), '-device-id'])
		proc.wait()
		if proc.poll() == 0:
			print("config already generated")
			return

		proc = subprocess.Popen([self.syncthing_bin, '-home={home}'.format(home=self.config_root), '-no-console', '-no-browser', '-no-restart', '-gui-address={addr}:{port}'.format(addr=self.syncthing_ip, port=self.syncthing_port), '-paused'], stdout=subprocess.PIPE)

		def _killproc(proc):
			proc.stdout.close()
			proc.terminate()
			time.sleep(5)
			if proc.poll() is None:
				proc.kill()
				proc.join()
		for i in xrange(100):
			line = proc.stdout.readline()
			if 'Default config saved' in line:
				_killproc(proc)
				return
		else:
			_killproc(proc)
			raise lance_utils.SyncthingError('unable to generate config!')

	def __del__(self):
		self.stop_syncthing()