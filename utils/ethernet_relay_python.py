#########################################################################################
#   License                                                                             #
#                                                                                       #
#   Copyright © 2018, Numato Systems Private Limited. All rights reserved.              #
#                                                                                       #
#   This software including all supplied files, Intellectual Property, know-how         #
#   Or part of thereof as applicable (collectively called SOFTWARE) in source           #
#   And/Or binary form with accompanying documentation Is licensed to you by            #
#   Numato Systems Private Limited (LICENSOR) subject To the following conditions.      #
#                                                                                       #
#   1. This license permits perpetual use of the SOFTWARE if all conditions in this     #
#       license are met. This license stands revoked In the Event Of breach Of any      #
#       of the conditions.                                                              #
#   2. You may use, modify, copy the SOFTWARE within your organization. This            #
#       SOFTWARE shall Not be transferred To third parties In any form except           #
#       fully compiled binary form As part Of your final application.                   #
#   3. This SOFTWARE Is licensed only to be used in connection with/executed on         #
#       supported products manufactured by Numato Systems Private Limited.              #
#       Using/ executing this SOFTWARE On/In connection With custom Or third party      #
#       hardware without the LICENSORs prior written permission Is expressly            #
#       prohibited.                                                                     #
#   4. You may Not download Or otherwise secure a copy of this SOFTWARE for the         #
#       purpose of competing with Numato Systems Private Limited Or subsidiaries in     #
#       any way such As but Not limited To sharing the SOFTWARE With competitors,       #
#       reverse engineering etc... You may Not Do so even If you have no gain           #
#       financial Or otherwise from such action.                                        #
#   5. DISCLAIMER                                                                       #
#   5.1. USING THIS SOFTWARE Is VOLUNTARY And OPTIONAL. NO PART OF THIS SOFTWARE        #
#       CONSTITUTE A PRODUCT Or PART Of PRODUCT SOLD BY THE LICENSOR.                   #
#   5.2. THIS SOFTWARE And DOCUMENTATION ARE PROVIDED “AS IS” WITH ALL FAULTS,          #
#       DEFECTS And ERRORS And WITHOUT WARRANTY OF ANY KIND.                            #
#   5.3. THE LICENSOR DISCLAIMS ALL WARRANTIES EITHER EXPRESS Or IMPLIED, INCLUDING     #
#       WITHOUT LIMITATION, ANY WARRANTY Of MERCHANTABILITY Or FITNESS For ANY          #
#       PURPOSE.                                                                        #
#   5.4. IN NO EVENT, SHALL THE LICENSOR, IT'S PARTNERS OR DISTRIBUTORS BE LIABLE OR    #
#       OBLIGATED FOR ANY DAMAGES, EXPENSES, COSTS, LOSS Of MONEY, LOSS OF TANGIBLE     #
#       Or INTANGIBLE ASSETS DIRECT Or INDIRECT UNDER ANY LEGAL ARGUMENT SUCH AS BUT    #
#       Not LIMITED TO CONTRACT, NEGLIGENCE, STRICT LIABILITY, CONTRIBUTION, BREACH     #
#       OF WARRANTY Or ANY OTHER SIMILAR LEGAL DEFINITION.                              #
#########################################################################################

#   Python code demonstrating basic Relay features Of Numato Lab Ethernet Relay Module.

#########################################################################################
#                                                                                       #
#                                   Prerequisites                                       #
#                                   -------------                                       #
#                                 Python version 3.x                                    #
#                                  pip version 6.x                                      #
#                                                                                       #
#########################################################################################

import sys
import telnetlib
import time
import re

#########################################################################################
#                                   Utility Functions                                   #
#########################################################################################

#Connect to device with user provided credentials
def connectToDevice(DeviceIPAddress):

    #Wait for login prompt from device and enter user name when prompted
    telnet_obj.read_until(b"login")
    telnet_obj.write(user.encode('ascii') + b"\r\n")
    
    #Wait for password prompt and enter password when prompted by device
    telnet_obj.read_until(b"Password: ")
    telnet_obj.write(password.encode('ascii') + b"\r\n")
    
    #Wait for device response
    log_result = telnet_obj.read_until(b"successfully\r\n")
    telnet_obj.read_until(b">")
    
    #Check if login attempt was successful
    if b"successfully" in log_result:
        return True
    elif "denied" in log_result:
        return False
        
        
#This code works with python3 and above
#Check the python version
if sys.version_info[0] < 3:
    raise Exception("Python version 3.x required")

DeviceIP = "169.254.1.1"                                                #Device IP address
user = "admin"                                                          #Device Telnet user name
password = "admin"                                                      #Device Telnet password
    
#Create a new TELNET object
telnet_obj = telnetlib.Telnet(DeviceIP)

#Connect to the device using credentials provided
if connectToDevice(DeviceIP) == True:
    print("\nLogged in successfully... Connected to device", DeviceIP, "\n")
else:
    print("Login failed!!!! Please check login credentials or Device IP Address\n\n")
    telnet_obj.close()
    exit(0)

#########################################################################################
#                                    Main application code                              #
#########################################################################################
  
#Flush any data that may be left in the input buffer
response = telnet_obj.read_eager()

#Enter Relay Number
relay_number = "2"                                                       #Change the Relay Number here when required

if re.match("^[a-v]*$", relay_number):
    relay_number = relay_number.upper();                                 #Converts the relay_number(A to V) to upper case if user entered it as lower case.

#########################################################################################

#Relay ON
telnet_obj.write(("relay on " + str(relay_number) + "\r\n").encode())
print("Relay ON", relay_number)
time.sleep(1)
telnet_obj.read_eager()
#########################################################################################


#Relay Read
telnet_obj.write(b"relay read " + str(relay_number).encode("ascii") + b"\r\n")
time.sleep(1)
response = telnet_obj.read_eager()
print("\nRelay read", relay_number, ":", re.split(br'[&>]', response)[0].decode())
#########################################################################################

#Relay OFF
telnet_obj.write(("relay off " + str(relay_number) + "\r\n").encode())
print("Relay OFF", relay_number)
time.sleep(1)
telnet_obj.read_eager()
#########################################################################################

#Relay Read
telnet_obj.write(b"relay read " + str(relay_number).encode("ascii") + b"\r\n")
time.sleep(1)
response = telnet_obj.read_eager()
print("\nRelay read", relay_number, ":", re.split(br'[&>]', response)[0].decode())
#########################################################################################

#Finally close the TELNET session before exiting
telnet_obj.close()

