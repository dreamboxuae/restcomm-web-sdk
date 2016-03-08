#! /usr/bin/env python

# Load testing Restcomm Media Server
#
# Example invocations:
# - Insecure invocation:
# $ ./restcomm-test.py --client-count 1 --client-url http://127.0.0.1:10510/webrtc-client.html --client-register-ws-url ws://127.0.0.1:5082 --client-register-domain 127.0.0.1 --client-username-prefix user --client-password 1234 --restcomm-account-sid ACae6e420f425248d6a26948c17a9e2acf --restcomm-auth-token 0d01c95aac798602579fe08fc2461036  --restcomm-base-url http://127.0.0.1:8080 --restcomm-phone-number "+5556" --restcomm-external-service-url http://127.0.0.1:10512/rcml
# - Secure invocation:
# $ restcomm-test.py --client-count 1 --client-url https://127.0.0.1:10511/webrtc-client.html --client-register-ws-url wss://54.146.130.52:5083 --client-register-domain 54.146.130.52 --client-username-prefix user --client-password 1234 --restcomm-account-sid ACae6e420f425248d6a26948c17a9e2acf --restcomm-auth-token 0d01c95aac798602579fe08fc2461036  --restcomm-base-url https://54.146.130.52 --restcomm-phone-number "+5556" --restcomm-external-service-url http://127.0.0.1:10512/rcml
#
# TODOs:
#
# - Fix the unprovisioning functionality also remove the Restcomm Clients and Restcomm Number
#

import argparse
import sys
import json
import time
import subprocess 
import os 
import re
import urllib
import urlparse
import signal

# Notice that we are using the dummy module which is implemented with threads,
# not multiple processes, as processes might be overkill in our situation (in
# case for example we want to spawn hundredths)
#
# To use multiple processes instead we should  use:
# import multiprocessing
# And replace ThreadPool with Pool
from multiprocessing.dummy import Pool as ThreadPool

# Selenium imports
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
import selenium.common.exceptions

# Globals
TAG = '[restcomm-test] '
# Keep the nodejs process in a global var so that we can reference it after the tests are over to shut it down
httpProcess = None
# Used in non-selenium runs
browserProcesses = list()

def threadFunction(dictionary): 
	try:
		print TAG + 'browser thread #' + str(dictionary['id']) + ' Running test for URL: ' + dictionary['url']

		chromeOptions = Options()
		# important: don't request permission for media
		chromeOptions.add_argument("--use-fake-ui-for-media-stream")
		# enable browser logging
		caps = DesiredCapabilities.CHROME
		#caps['loggingPrefs'] = {'browser': 'ALL', 'client': 'ALL', 'driver': 'ALL', 'performance': 'ALL', 'server': 'ALL'}
		caps['loggingPrefs'] = { 'browser':'ALL' }
		#driver = webdriver.Chrome(chrome_options = chromeOptions, desired_capabilities = caps, service_args = ["--verbose", "--log-path=chrome.log"])
		driver = webdriver.Chrome(chrome_options = chromeOptions, desired_capabilities = caps)

		# navigate to web page
		driver.get(dictionary['url'])
		#print driver.title

		#print 'Waiting for condition to be met'
		#WebDriverWait(driver, 30).until(expected_conditions.text_to_be_present_in_element((By.ID,'log'), 'Connection ended'))
		# this is actually a hack to keep the browser open for n seconds. Putting the thread to sleep doesn't work and so far I haven't found a nice way to do that in Selenium
		WebDriverWait(driver, 300).until(expected_conditions.text_to_be_present_in_element((By.ID,'log'), 'Non existing text'))
		

	except selenium.common.exceptions.TimeoutException as ex:
		print TAG + 'EXCEPTION: browser thread #' + str(dictionary['id']) + ' Test timed out'
	except:
		print TAG + 'EXCEPTION: browser thread #' +  str(dictionary['id']) + ' Unexpected exception: ', sys.exc_info()[0]
		return

	# print messages
	print TAG + 'browser thread #' + str(dictionary['id']) + ' Saving the logs'
	logBuffer = ''
	for entry in driver.get_log('browser'):
		# entry is a dictionary
		logBuffer += json.dumps(entry, indent = 3)

	logFile = open('browser#' + str(dictionary['id']) + '.log', 'a')
	logFile.write(logBuffer)
	logFile.close()

	print TAG + 'browser thread #' + str(dictionary['id']) + ' Closing Driver'
	driver.close()

def signalHandler(signal, frame):
	print('User interrupted testing with SIGINT; bailing out')
	stopServer()
	sys.exit(0)

# take a url and break it in protocol and transport counterparts
def breakUrl(url):
	matches = re.search('(^.*?:\/\/)(.*$)', url)
	protocol = matches.group(1)
	transport = matches.group(2)
	return protocol, transport

# Return the account base Restcomm URL, like http://ACae6e420f425248d6a26948c17a9e2acf:0d01c95aac798602579fe08fc2461036@127.0.0.1:8080/restcomm/2012-04-24/Accounts/ACae6e420f425248d6a26948c17a9e2acf,
# from base URL (i.e. http://127.0.0.1:8080), account sid and auth token
def restBaseUrlFromCounterparts(accountSid, authToken, restcommUrl):
	# Need to break URL in protocol and transport parts so that we can put account sid and auth token in between 
	matches = re.search('(^.*?:\/\/)(.*$)', restcommUrl)
	#protocol = matches.group(1)
	#transport = matches.group(2)
	protocol, transport = breakUrl(restcommUrl)
	return protocol + accountSid + ':' + authToken + '@' + transport + '/restcomm/2012-04-24/Accounts/' + accountSid

# curl will break if we target an https server that has self signed certificate. Let's always use -k (avoid checks for cert) when targeting https
def curlSecureOptionsIfApplicable(restcommUrl):
	protocol, transport = breakUrl(restcommUrl)
	if protocol == 'https://':
		return '-k'
	else:
		return ''

# Provision Restcomm Number for external service via REST call
def provisionPhoneNumber(phoneNumber, externalServiceUrl, accountSid, authToken, restcommUrl): 
	print TAG + "Provisioning phone number " + phoneNumber + ' and linking it with Voice URL: ' + externalServiceUrl
	devnullFile = open(os.devnull, 'w')
	# Need to break URL in protocol and transport parts so that we can put account sid and auth token in between 
	matches = re.search('(^.*?:\/\/)(.*$)', restcommUrl)
	protocol = matches.group(1)
	transport = matches.group(2)
	postData = {
		'PhoneNumber': phoneNumber,
		'VoiceUrl': externalServiceUrl,
		'VoiceMethod': 'GET',
		'FriendlyName': 'Load Testing App',
		'isSIP' : 'true',
	}
	#cmd = 'curl -X POST ' + restBaseUrlFromCounterparts(accountSid, authToken, restcommUrl) + '/IncomingPhoneNumbers.json -d PhoneNumber=' + phoneNumber + ' -d VoiceUrl=' + externalServiceUrl + ' -d FriendlyName=LoadTestingApp -d isSIP=true'
	cmd = 'curl ' + curlSecureOptionsIfApplicable(restcommUrl) + ' -X POST ' + restBaseUrlFromCounterparts(accountSid, authToken, restcommUrl) + '/IncomingPhoneNumbers.json -d ' + urllib.urlencode(postData)
	print TAG + cmd 
	subprocess.call(cmd.split(), stdout = devnullFile, stderr = devnullFile)


# Provision Restcomm Clients via REST call
# count: number of Clients to provision
# accountSid: Restcomm accountSid, like: ACae6e420f425248d6a26948c17a9e2acf
# authToken: Restcomm authToken, like: 0a01c34aac72a432579fe08fc2461036 
# restcommUrl: Restcomm URL, like: http://127.0.0.1:8080
def provisionClients(count, accountSid, authToken, restcommUrl, usernamePrefix, password): 
	print TAG + "Provisioning " + str(count) + " Restcomm Clients"
	devnullFile = open(os.devnull, 'w')
	# Need to break URL in protocol and transport parts so that we can put account sid and auth token in between 
	matches = re.search('(^.*?:\/\/)(.*$)', restcommUrl)
	protocol = matches.group(1)
	transport = matches.group(2)
	for i in range(1, count + 1):
		postData = {
			'Login': usernamePrefix + str(i),
			'Password': password,
		}
		#cmd = 'curl -X POST ' + restBaseUrlFromCounterparts(accountSid, authToken, restcommUrl) + '/Clients.json -d Login=user' + str(i) + ' -d Password=1234'
		cmd = 'curl ' + curlSecureOptionsIfApplicable(restcommUrl) + ' -X POST ' + restBaseUrlFromCounterparts(accountSid, authToken, restcommUrl) + '/Clients.json -d ' + urllib.urlencode(postData)
		#system(cmd)
		print TAG + cmd 
		subprocess.call(cmd.split(), stdout = devnullFile, stderr = devnullFile)

def startServer(count, clientUrl, externalServiceUrl, usernamePrefix): 
	print TAG + 'Starting http server to handle both http/https request for the webrtc-client web page, and RCML REST requests from Restcomm'

	externalServicePort = '80'
	externalServiceParsedUrl = urlparse.urlparse(externalServiceUrl);
	if externalServiceParsedUrl.port:
		externalServicePort = externalServiceParsedUrl.port
	
	webAppPort = '80'
	clientParsedUrl = urlparse.urlparse(clientUrl);
	if (clientParsedUrl.port):
		webAppPort = clientParsedUrl.port

	secureArg = ''
	if clientParsedUrl.scheme == 'https':
		secureArg = '--secure-web-app'
	
	# Make a copy of the current environment
	envDictionary = dict(os.environ)   
	# Add the nodejs path, as it isn't found when we run as root
	envDictionary['NODE_PATH'] = '/usr/local/lib/node_modules'
	#cmd = 'server.js ' + str(count) + ' 10512 10510 10511'
	cmd = 'node http-server.js --client-count ' + str(count) + ' --external-service-port ' + str(externalServicePort) + ' --external-service-client-prefix ' + usernamePrefix + ' --web-app-port ' + str(webAppPort) + ' ' + secureArg
	# We want it to run in the background
	#os.system(cmd)
	#subprocess.call(cmd.split(), env = envDictionary)
	#print "--- CMD: " + cmd
	global httpProcess
	httpProcess = subprocess.Popen(cmd.split(), env = envDictionary)
	#httpProcess = subprocess.Popen(cmd.split())
	print TAG + 'PID for http server: ' + str(httpProcess.pid)

# TODO: Not finished yet
def unprovisionClients(count, accountSid, authToken, restcommUrl): 
	print TAG + "(Not implemented yet) Unprovisioning " + str(count) + " Restcomm Clients"
	#for i in range(1, count + 1):
	#	cmd = 'curl ' + curlSecureOptionsIfApplicable(restcommUrl) + ' -X DELETE http://' + accountSid + ':' + authToken + '@' + transport + '/restcomm/2012-04-24/Accounts/' + accountSid + '/Clients.json -d Login=user' + str(i) + ' -d Password=1234'
	#	...

def stopServer(): 
	if httpProcess:
		print TAG + 'Stopping http server'
		httpProcess.terminate()

def globalSetup(dictionary): 
	print TAG + "Setting up tests"

	# Provision Restcomm with the needed Clients
	provisionPhoneNumber(dictionary['phone-number'], dictionary['external-service-url'], dictionary['account-sid'], dictionary['auth-token'], dictionary['restcomm-base-url'])

	# Provision Restcomm with the needed Clients
	provisionClients(dictionary['count'], dictionary['account-sid'], dictionary['auth-token'], dictionary['restcomm-base-url'], dictionary['username-prefix'], dictionary['password'])

	# Start the unified server script to serve both RCML (REST) and html page for webrtc clients to connect to
	startServer(dictionary['count'], dictionary['client-url'], dictionary['external-service-url'], dictionary['username-prefix'])

def globalTeardown(dictionary): 
	print TAG + "Tearing down tests"

	# Provision Restcomm with the needed Clients
	unprovisionClients(dictionary['count'], dictionary['account-sid'], dictionary['auth-token'], dictionary['restcomm-base-url'])

	# Start the unified server script to serve both RCML (REST) and html page for webrtc clients to connect to
	stopServer()

## --------------- Main code --------------- ##

parser = argparse.ArgumentParser()
parser.add_argument('-c', '--client-count', dest = 'count', default = 10, type = int, help = 'Count of Webrtc clients spawned for the test')
parser.add_argument('--client-url', dest = 'clientUrl', default = 'http://127.0.0.1:10510/webrtc-client.html', help = 'Webrtc clients target URL, like \'http://127.0.0.1:10510/webrtc-client.html\'')
parser.add_argument('--client-register-ws-url', dest = 'registerWsUrl', default = 'ws://127.0.0.1:5082', help = 'Webrtc clients target websocket URL for registering, like \'ws://127.0.0.1:5082\'')
parser.add_argument('--client-register-domain', dest = 'registerDomain', default = '127.0.0.1', help = 'Webrtc clients domain for registering, like \'127.0.0.1\'')
parser.add_argument('--client-username-prefix', dest = 'usernamePrefix', default = 'user', help = 'User prefix for the clients, like \'user\'')
parser.add_argument('--client-password', dest = 'password', default = '1234', help = 'Password for the clients, like \'1234\'')
parser.add_argument('--restcomm-base-url', dest = 'restcommBaseUrl', default = 'http://127.0.0.1:8080', help = 'Restcomm instance base URL, like \'http://127.0.0.1:8080\'')
parser.add_argument('--restcomm-account-sid', dest = 'accountSid', required = True, help = 'Restcomm accound Sid, like \'ACae6e420f425248d6a26948c17a9e2acf\'')
parser.add_argument('--restcomm-auth-token', dest = 'authToken', required = True, help = 'Restcomm auth token, like \'0a01c34aac72a432579fe08fc2461036\'')
parser.add_argument('--restcomm-phone-number', dest = 'phoneNumber', default = '+5556', help = 'Restcomm phone number to provision and link with external service, like \'+5556\'')
parser.add_argument('--restcomm-external-service-url', dest = 'externalServiceUrl', default = 'http://127.0.0.1:10512/rcml', help = 'External service URL for Restcomm to get RCML from, like \'http://127.0.0.1:10512/rcml\'')
args = parser.parse_args()

print TAG + 'Webrtc clients settings: \n\tcount: ' + str(args.count) + '\n\ttarget URL: ' + args.clientUrl + '\n\tregister websocket url: ' + args.registerWsUrl + '\n\tregister domain: ' + args.registerDomain + '\n\tusername prefix: ' + args.usernamePrefix + '\n\tpassword: ' + args.password
print TAG + 'Restcomm instance settings: \n\tbase URL: ' + args.restcommBaseUrl + '\n\taccount sid: ' + args.accountSid + '\n\tauth token: ' + args.authToken + '\n\tphone number: ' + args.phoneNumber + '\n\texternal service URL: ' + args.externalServiceUrl
# Let's handle sigint so the if testing is interrupted we still cleanup
# After handling the exceptions we no longer need this
#signal.signal(signal.SIGINT, signalHandler)

globalSetup({ 
	'count': args.count, 
	'client-url': args.clientUrl, 
	'username-prefix': args.usernamePrefix,
	'password': args.password,
	'account-sid': args.accountSid, 
	'auth-token': args.authToken, 
	'restcomm-base-url': args.restcommBaseUrl,
	'phone-number': args.phoneNumber, 
	'external-service-url': args.externalServiceUrl
})

# Populate a list with browser thread ids and URLs for each client thread that will be spawned
clients = list()
for i in range(1, args.count + 1):
	getData = {
		'username': args.usernamePrefix + str(i),
		'password': args.password,
		'register-ws-url': args.registerWsUrl,
		'register-domain' : args.registerDomain,
	}
	clients.append({ 
		'id': i, 
		#'url' : args.clientUrl + '?username=' + args.usernamePrefix + str(i) + '&password=' + args.password + '&register-ws-url=' + args.registerWsUrl + '&register-domain=' + args.registerDomain,
		'url' : args.clientUrl + '?' + urllib.urlencode(getData)
	})

useSelenium = False;
if useSelenium:
	print TAG + 'Spawning ' + str(args.count) + ' tester threads' 
	# Make the Pool of workers
	pool = ThreadPool(args.count) 
	# Open the urls in their own threads and return the results
	try:
		results = pool.map(threadFunction, clients)
	except:
		print TAG + 'EXCEPTION: pool.map() failed. Unexpected exception: ', sys.exc_info()[0]

	# close the pool and wait for the work to finish 
	pool.close() 
	pool.join() 
else:
	#for i in range(1, args.count + 1):
	for client in clients:
		# Make a copy of the current environment
		envDictionary = dict(os.environ)   
		# Set the chrome log file
		envDictionary['CHROME_LOG_FILE'] = 'browser#' + str(client['id']) + '.log'
		cmdList = [ 
			'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',  
			client['url'], 
			' --enable-logging',
			'--use-fake-ui-for-media-stream',
		]
		# We want it to run in the background
		separator = ' '
		print TAG + "Spawning browser #" + str(client['id']) + ', command: ' + separator.join(cmdList)
		devnullFile = open(os.devnull, 'w')
		client['process'] = subprocess.Popen(cmdList, env = envDictionary, stdout = devnullFile, stderr = devnullFile)
		#browserProcesses.append(subprocess.Popen(cmd.split(), env = envDictionary))
	

# raw_input doesn't exist in 3.0 and inputString issues an error in 2.7
if (sys.version_info < (3, 0)):
	inputString = raw_input(TAG + 'Press any key to stop the test...\n')
else:
	inputString = input(TAG + 'Press any key to stop the test...')

if not useSelenium:
	#for i in range(1, args.count + 1):
	for client in clients:
		print TAG + "Stopping client #" + str(client['id']) 
		client['process'].terminate()

globalTeardown({ 
	'count': args.count, 
	'username': args.password,
	'password': args.password,
	'account-sid': args.accountSid, 
	'auth-token': args.authToken, 
	'restcomm-base-url': args.restcommBaseUrl,
	'phone-number': args.phoneNumber, 
	'external-service-url': args.externalServiceUrl
})
