import os
import sqlite3
import time
from .servercomponent import ServerComponent
from .lance_utils import async_method
import xml.etree.ElementTree as ET
import re


class DatabaseHandler(ServerComponent):
    def __init__(self, server, databasepath):
        super(DatabaseHandler, self).__init__(server)
        self.__db = databasepath

    # def run(self):
    # 	while not self._stopped_set():
    # 		con = sqlite3.connect(self.__db, 30)
    # 		try:
    # 			cur = con.cursor()
    #
    # 		finally:
    # 			con.close()
    # 		time.sleep(30)

    # @async_method
    # def generate_base_config(self):
    # 	'''
    # 	generates base config for folders and devices
    # 	:return:
    # 	'''
    # 	conf = ET.Element('configuration', {'version': '28'})
    # 	con = sqlite3.connect(self.__db, 30)
    # 	try:
    # 		cur = con.cursor()
    # 		good_devices = cur.execute("SELECT device,address FROM devices INNER JOIN users ON devices.userid=users.userid WHERE devices.blocked=0 AND users.access<>0").fetchall()
    # 		bad_devices = cur.execute("SELECT device FROM devices INNER JOIN users ON devices.userid=users.userid WHERE devices.blocked=1 OR users.access=0").fetchall()
    # 		for dev,adr in good_devices:
    # 			dvc = ET.SubElement(conf, 'device', {'id': dev, 'compression': 'metadata', 'introducer':'false'})
    # 			ET.SubElement(dvc, 'address').text = 'dynamic' if adr is None else adr
    # 		for dev in bad_devices:
    # 			ET.SubElement(conf, 'ignoredDevice').text = dev
    #
    # 		folders = cur.execute("SELECT id,label,path from folders").fetchall()
    # 		for fid, flabel, fpath in folders:
    # 			acs = cur.execute("SELECT users.userid,users.access,devices.device FROM folders_access INNER JOIN users ON folders_access.userid=users.userid INNER JOIN devices ON users.userid=devices.userid WHERE users.access<>0 AND folders_access.id=:fid", {'fid': fid}).fetchall()
    #
    # 			for i in xrange(9999): #reasonable 'something is wrong' condition
    # 				#search config entries
    # 				match = re.search('__LANCECONF_(\w+)__', fpath)
    # 				if match and match.group(1) in self.config():
    # 					fpath = ''.join([fpath[:match.start(0)], self.config()[match.group(1)], fpath[match.end(0):]])
    # 				#search env
    # 				match = re.search('__LANCEENV_(\w+)__', fpath)
    # 				if match and match.group(1) in os.environ:
    # 					fpath = ''.join([fpath[:match.start(0)], os.environ[match.group(1)], fpath[match.end(0):]])
    # 				#search local(to current execution) variables
    # 				locs = {'folder_label': flabel}
    # 				match = re.search('__LANCELOC_(\w+)__', fpath)
    # 				if match and match.group(1) in locs:
    # 					fpath = ''.join([fpath[:match.start(0)], locs[match.group(1)], fpath[match.end(0):]])
    #
    #
    # 			fold = ET.SubElement(conf, 'folder', {'id': fid, 'label': flabel, 'path':os.path.normpath(fpath), 'type': 'sendreceive'})
    # 			for _, _, dev in acs:
    # 				ET.SubElement(fold, 'device', {'id': dev})
    #
    # 	finally:
    # 		con.close()
    #
    # 	return conf

    @async_method
    def get_configuration(self):
        """

        :return:
        """
        cfg = {'devices': {}, 'blacklist': [], 'folders': {}}
        con = sqlite3.connect(self.__db, 30)
        try:
            cur = con.cursor()
            good_devices = cur.execute(
                "SELECT device,address FROM devices INNER JOIN users ON devices.userid=users.userid WHERE devices.blocked=0 AND users.access<>0").fetchall()
            bad_devices = cur.execute(
                "SELECT device FROM devices INNER JOIN users ON devices.userid=users.userid WHERE devices.blocked=1 OR users.access=0").fetchall()
            for dev, adr in good_devices:
                cfg['devices'][dev] = {'address': 'dynamic' if adr is None else adr, }
            for dev in bad_devices:
                cfg['blacklist'].append(dev)

            folders = cur.execute("SELECT id,label,path from folders").fetchall()
            for fid, flabel, fpath in folders:
                acs = cur.execute(
                    "SELECT users.userid,users.access,devices.device FROM folders_access INNER JOIN users ON folders_access.userid=users.userid INNER JOIN devices ON users.userid=devices.userid WHERE users.access<>0 AND folders_access.id=:fid",
                    {'fid': fid}).fetchall()

                for i in range(9999):  # reasonable 'something is wrong' condition
                    # search config entries
                    match = re.search('__LANCECONF_(\w+)__', fpath)
                    if match and match.group(1) in self.config():
                        fpath = ''.join([fpath[:match.start(0)], self.config()[match.group(1)], fpath[match.end(0):]])
                    # search env
                    match = re.search('__LANCEENV_(\w+)__', fpath)
                    if match and match.group(1) in os.environ:
                        fpath = ''.join([fpath[:match.start(0)], os.environ[match.group(1)], fpath[match.end(0):]])
                    # search local(to current execution) variables
                    locs = {'folder_label': flabel}
                    match = re.search('__LANCELOC_(\w+)__', fpath)
                    if match and match.group(1) in locs:
                        fpath = ''.join([fpath[:match.start(0)], locs[match.group(1)], fpath[match.end(0):]])

                cfg['folders'][fid] = {'label': flabel, 'path': os.path.normpath(fpath), 'type': 'sendreceive',
                                       'devices': []}
                for _, _, dev in acs:
                    cfg['folders'][fid]['devices'].append(dev)
        finally:
            con.close()

        return cfg

    @async_method
    def get_devices(self):
        '''
        query DB and get active device list
        :return: list of dicts with 'device', 'userid' keys
        '''
        con = sqlite3.connect(self.__db, 30)
        try:
            cur = con.cursor()
            devices = cur.execute(
                "SELECT device,userid FROM devices INNER JOIN users ON devices.userid=users.userid WHERE devices.blocked=0 AND users.access<>0").fetchall()
        finally:
            con.close()

        return [{'device': dev, 'userid': uid} for dev, uid in devices]


if __name__ == '__main__':
    import queue
    from xml.dom import minidom

    print('testing')


    class Srvtest():
        def __init__(self):
            self.config = {'config_root': os.path.join(os.path.split(os.path.abspath(__file__))[0], r'config'),
                           'data_root': os.path.join(os.path.split(os.path.abspath(__file__))[0], r'data')}
            self.eventQueue = queue.Queue()


    s = Srvtest()
    dbh = DatabaseHandler(s, os.path.join(os.path.split(os.path.abspath(__file__))[0], r'test.sqlite3'))
    dbh.start()
    res = dbh.generate_base_config()
    print(minidom.parseString(ET.tostring(res.result(), encoding='UTF-8', )).toprettyxml())
