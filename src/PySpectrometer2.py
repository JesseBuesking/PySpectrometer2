#!/usr/bin/env python3

'''
PySpectrometer2 Les Wright 2022
https://www.youtube.com/leslaboratory
https://github.com/leswright1977

This project is a follow on from: https://github.com/leswright1977/PySpectrometer 

This is a more advanced, but more flexible version of the original program. Tk Has been dropped as the GUI to allow fullscreen mode on Raspberry Pi systems and the iterface is designed to fit 800*480 screens, which seem to be a common resolutin for RPi LCD's, paving the way for the creation of a stand alone benchtop instrument.

Whats new:
Higher resolution (800px wide graph)
3 row pixel averaging of sensor data
Fullscreen option for the Spectrometer graph
3rd order polymonial fit of calibration data for accurate measurement.
Improved graph labelling
Labelled measurement cursors
Optional waterfall display for recording spectra changes over time.
Key Bindings for all operations

All old features have been kept, including peak hold, peak detect, Savitsky Golay filter, and the ability to save graphs as png and data as CSV.

For instructions please consult the readme!
'''


import cv2
import time
import numpy as np
from specFunctions import wavelength_to_rgb,savitzky_golay,peakIndexes,readcal,writecal,background,generateGraticule
import base64
import argparse
from pprint import pprint

parser = argparse.ArgumentParser()
parser.add_argument("--device", type=int, help="Video Device number e.g. 0, use v4l2-ctl --list-devices")
parser.add_argument("--webcam", action=argparse.BooleanOptionalAction, help="Use the first available camera")
parser.add_argument("--picam", action=argparse.BooleanOptionalAction, help="Use the picamera")
parser.add_argument("--fps", type=int, default=30, help="Frame Rate e.g. 30")
group = parser.add_mutually_exclusive_group()
group.add_argument("--fullscreen", help="Fullscreen (Native 800*480)",action="store_true")
group.add_argument("--waterfall", help="Enable Waterfall (Windowed only)",action="store_true")
args = parser.parse_args()
dispFullscreen = False
dispWaterfall = False
if args.fullscreen:
	print("Fullscreen Spectrometer enabled")
	dispFullscreen = True
if args.waterfall:
	print("Waterfall display enabled")
	dispWaterfall = True

use_webcam = args.webcam is not None
use_device = args.device is not None
use_picamera = args.picam is not None

fps = args.fps
dev = 0
if args.device:
	dev = args.device
	

frameWidth = 800
frameHeight = 600
message_loc1 = frameWidth - 310
message_loc2 = frameWidth - 160

picam2 = None
if use_picamera:
	from libcamera import controls
	from picamera2 import Picamera2

	picam2 = Picamera2()
	#need to spend more time at: https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf
	#but this will do for now!
	#min and max microseconds per frame gives framerate.
	#30fps (33333, 33333)
	#25fps (40000, 40000)

	NoiseReductionMode = controls.draft.NoiseReductionModeEnum
	picamGain = 10.0
	video_config = picam2.create_video_configuration(
		main={
			"format": 'RGB888',
			"size": (frameWidth, frameHeight)
		},
		controls={
			"NoiseReductionMode": NoiseReductionMode.Fast,
			"FrameDurationLimits": (33333, 33333),
			"AnalogueGain": picamGain
		})
	pprint(video_config["controls"])
	picam2.configure(video_config)
	picam2.start()

	#Change analog gain
	#picam2.set_controls({"AnalogueGain": 10.0}) #Default 1
	#picam2.set_controls({"Brightness": 0.2}) #Default 0 range -1.0 to +1.0
	#picam2.set_controls({"Contrast": 1.8}) #Default 1 range 0.0-32.0

cap = None
if use_device or use_webcam:
	if use_device:
		cap = cv2.VideoCapture('/dev/video'+str(dev), cv2.CAP_V4L)
	elif use_webcam:
		cap = cv2.VideoCapture(0)
	print("[info] W, H, FPS")
	cap.set(cv2.CAP_PROP_FRAME_WIDTH,frameWidth)
	cap.set(cv2.CAP_PROP_FRAME_HEIGHT,frameHeight)
	cap.set(cv2.CAP_PROP_FPS,fps)
	print(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
	print(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
	print(cap.get(cv2.CAP_PROP_FPS))
	cfps = (cap.get(cv2.CAP_PROP_FPS))


title1 = 'PySpectrometer 2 - Spectrograph'
title2 = 'PySpectrometer 2 - Waterfall'
stackHeight = 320+80+80 #height of the displayed CV window (graph+preview+messages)

if dispWaterfall == True:
	#watefall first so spectrum is on top
	cv2.namedWindow(title2,cv2.WINDOW_GUI_NORMAL)
	cv2.resizeWindow(title2,frameWidth,stackHeight)
	cv2.moveWindow(title2,200,200);

if dispFullscreen == True:
	cv2.namedWindow(title1,cv2.WND_PROP_FULLSCREEN)
	cv2.setWindowProperty(title1,cv2.WND_PROP_FULLSCREEN,cv2.WINDOW_FULLSCREEN)
else:
	cv2.namedWindow(title1,cv2.WINDOW_GUI_NORMAL)
	cv2.resizeWindow(title1,frameWidth,stackHeight)
	cv2.moveWindow(title1,0,0);

#settings for peak detect
savpoly = 7 #savgol filter polynomial max val 15
mindist = 50 #minumum distance between peaks max val 100
thresh = 20 #Threshold max val 100

calibrate = False

clickArray = [] 
cursorX = 0
cursorY = 0
def handle_mouse(event,x,y,flags,param):
	global clickArray
	global cursorX
	global cursorY
	mouseYOffset = 160
	if event == cv2.EVENT_MOUSEMOVE:
		cursorX = x
		cursorY = y	
	if event == cv2.EVENT_LBUTTONDOWN:
		mouseX = x
		mouseY = y-mouseYOffset
		clickArray.append([mouseX,mouseY])
#listen for click on plot window
cv2.setMouseCallback(title1,handle_mouse)


font=cv2.FONT_HERSHEY_SIMPLEX

intensity = [0] * frameWidth #array for intensity data...full of zeroes

holdpeaks = False #are we holding peaks?
measure = False #are we measuring?
recPixels = False #are we measuring pixels and recording clicks?


#messages
msg1 = ""
saveMsg = "No data saved"

#blank image for Waterfall
waterfall = np.zeros([320,frameWidth,3],dtype=np.uint8)
waterfall.fill(0) #fill black

#Go grab the computed calibration data
caldata = readcal(frameWidth)
wavelengthData = caldata[0]
calmsg1 = caldata[1]
calmsg2 = caldata[2]
calmsg3 = caldata[3]

#generate the craticule data
graticuleData = generateGraticule(wavelengthData)
tens = (graticuleData[0])
fifties = (graticuleData[1])


def snapshot(savedata):
	now = time.strftime("%Y%m%d--%H%M%S")
	timenow = time.strftime("%H:%M:%S")
	imdata1 = savedata[0]
	graphdata = savedata[1]
	if dispWaterfall == True:
		imdata2 = savedata[2]
		cv2.imwrite("waterfall-" + now + ".png",imdata2)
	cv2.imwrite("spectrum-" + now + ".png",imdata1)
	#print(graphdata[0]) #wavelengths
	#print(graphdata[1]) #intensities
	f = open("Spectrum-"+now+'.csv','w')
	f.write('Wavelength,Intensity\r\n')
	for x in zip(graphdata[0],graphdata[1]):
		f.write(str(x[0])+','+str(x[1])+'\r\n')
	f.close()
	message = "Last Save: "+timenow
	return(message)


while (True if use_picamera else cap.isOpened()):
	# Capture frame-by-frame
	frame = None
	ret = True
	if use_picamera:
		frame = picam2.capture_array()
	else:
		ret, frame = cap.read()

	if not ret:
		break

	y=int((frameHeight/2)-40) #origin of the vertical crop
	#y=200 	#origin of the vert crop
	x=0   	#origin of the horiz crop
	h=80 	#height of the crop
	w=frameWidth 	#width of the crop
	cropped = frame[y:y+h, x:x+w]
	bwimage = cv2.cvtColor(cropped,cv2.COLOR_BGR2GRAY)
	rows,cols = bwimage.shape
	halfway =int(rows/2)
	#show our line on the original image
	#now a 3px wide region
	cv2.line(cropped,(0,halfway-2),(frameWidth,halfway-2),(255,255,255),1)
	cv2.line(cropped,(0,halfway+2),(frameWidth,halfway+2),(255,255,255),1)

	#banner image
	decoded_data = base64.b64decode(background)
	np_data = np.frombuffer(decoded_data,np.uint8)
	img = cv2.imdecode(np_data,3)
	messages = img

	# the background is fixed to 800x80, so we need to scale it if
	# frameWidth and frameHeight have changed
	img_resized = np.zeros([img.shape[0], frameWidth, 3], dtype=np.uint8)
	w1 = img.shape[1]
	w2 = img_resized.shape[1]
	xoff = round((w2-w1)/2)
	# center the background img in the newly sized background
	img_resized[:, xoff:xoff+w1,:] = img
	messages = img_resized

	#blank image for Graph
	graph = np.zeros([320,frameWidth,3],dtype=np.uint8)
	graph.fill(255) #fill white

	#Display a graticule calibrated with cal data
	textoffset = 12
	#vertial lines every whole 10nm
	for position in tens:
		cv2.line(graph,(position,15),(position,320),(200,200,200),1)

	#vertical lines every whole 50nm
	for positiondata in fifties:
		cv2.line(graph,(positiondata[0],15),(positiondata[0],320),(0,0,0),1)
		cv2.putText(graph,str(positiondata[1])+'nm',(positiondata[0]-textoffset,12),font,0.4,(0,0,0),1, cv2.LINE_AA)

	#horizontal lines
	for i in range (320):
		if i>=64:
			if i%64==0: #suppress the first line then draw the rest...
				cv2.line(graph,(0,i),(frameWidth,i),(100,100,100),1)
	
	#Now process the intensity data and display it
	#intensity = []
	for i in range(cols):
		#data = bwimage[halfway,i] #pull the pixel data from the halfway mark	
		#print(type(data)) #numpy.uint8
		#average the data of 3 rows of pixels:
		dataminus1 = bwimage[halfway-1,i]
		datazero = bwimage[halfway,i] #pull the pixel data from the halfway mark
		dataplus1 = bwimage[halfway+1,i]
		data = (int(dataminus1)+int(datazero)+int(dataplus1))/3		
		data = np.uint8(data)
				
		
		if holdpeaks == True:
			if data > intensity[i]:
				intensity[i] = data
		else:
			intensity[i] = data

	if dispWaterfall == True:
		#waterfall....
		#data is smoothed at this point!!!!!!
		#create an empty array for the data
		wdata = np.zeros([1,frameWidth,3],dtype=np.uint8)
		index=0
		for i in intensity:
			rgb = wavelength_to_rgb(round(wavelengthData[index]))#derive the color from the wavelenthData array
			luminosity = intensity[index]/255
			b = int(round(rgb[0]*luminosity))
			g = int(round(rgb[1]*luminosity))
			r = int(round(rgb[2]*luminosity))
			#print(b,g,r)
			#wdata[0,index]=(r,g,b) #fix me!!! how do we deal with this data??
			wdata[0,index]=(r,g,b)
			index+=1

		if use_picamera:
			#bright and contrast of final image
			contrast = 2.5
			brightness =10
			wdata = cv2.addWeighted( wdata, contrast, wdata, 0, brightness)

		waterfall = np.insert(waterfall, 0, wdata, axis=0) #insert line to beginning of array
		waterfall = waterfall[:-1].copy() #remove last element from array

		hsv = cv2.cvtColor(waterfall, cv2.COLOR_BGR2HSV)


	#Draw the intensity data :-)
	#first filter if not holding peaks!
	
	if holdpeaks == False:
		intensity = savitzky_golay(intensity,17,savpoly)
		intensity = np.array(intensity)
		intensity = intensity.astype(int)
		holdmsg = "Holdpeaks OFF" 
	else:
		holdmsg = "Holdpeaks ON"
		
	
	#now draw the intensity data....
	index=0
	for i in intensity:
		rgb = wavelength_to_rgb(round(wavelengthData[index]))#derive the color from the wvalenthData array
		r = rgb[0]
		g = rgb[1]
		b = rgb[2]
		#or some reason origin is top left.
		cv2.line(graph, (index,320), (index,320-i), (b,g,r), 1)
		cv2.line(graph, (index,319-i), (index,320-i), (0,0,0), 1,cv2.LINE_AA)
		index+=1


	#find peaks and label them
	textoffset = 12
	thresh = int(thresh) #make sure the data is int.
	indexes = peakIndexes(intensity, thres=thresh/max(intensity), min_dist=mindist)
	
	for i in indexes:
		height = intensity[i]
		height = 310-height
		wavelength = round(wavelengthData[i],1)
		cv2.rectangle(graph,((i-textoffset)-2,height),((i-textoffset)+60,height-15),(0,255,255),-1)
		cv2.rectangle(graph,((i-textoffset)-2,height),((i-textoffset)+60,height-15),(0,0,0),1)
		cv2.putText(graph,str(wavelength)+'nm',(i-textoffset,height-3),font,0.4,(0,0,0),1, cv2.LINE_AA)
		#flagpoles
		cv2.line(graph,(i,height),(i,height+10),(0,0,0),1)


	if measure == True:
		#show the cursor!
		cv2.line(graph,(cursorX,cursorY-140),(cursorX,cursorY-180),(0,0,0),1)
		cv2.line(graph,(cursorX-20,cursorY-160),(cursorX+20,cursorY-160),(0,0,0),1)
		cv2.putText(graph,str(round(wavelengthData[cursorX],2))+'nm',(cursorX+5,cursorY-165),font,0.4,(0,0,0),1, cv2.LINE_AA)

	if recPixels == True:
		#display the points
		cv2.line(graph,(cursorX,cursorY-140),(cursorX,cursorY-180),(0,0,0),1)
		cv2.line(graph,(cursorX-20,cursorY-160),(cursorX+20,cursorY-160),(0,0,0),1)
		cv2.putText(graph,str(cursorX)+'px',(cursorX+5,cursorY-165),font,0.4,(0,0,0),1, cv2.LINE_AA)
	else:
		#also make sure the click array stays empty
		clickArray = []

	if clickArray:
		for data in clickArray:
			mouseX=data[0]
			mouseY=data[1]
			cv2.circle(graph,(mouseX,mouseY),5,(0,0,0),-1)
			#we can display text :-) so we can work out wavelength from x-pos and display it ultimately
			cv2.putText(graph,str(mouseX),(mouseX+5,mouseY),cv2.FONT_HERSHEY_SIMPLEX,0.4,(0,0,0))
	



	#stack the images and display the spectrum	
	spectrum_vertical = np.vstack((messages, cropped, graph))
	#dividing lines...
	cv2.line(spectrum_vertical,(0,80),(frameWidth,80),(255,255,255),1)
	cv2.line(spectrum_vertical,(0,160),(frameWidth,160),(255,255,255),1)
	#print the messages
	cv2.putText(spectrum_vertical,calmsg1,(message_loc1,15),font,0.4,(0,255,255),1, cv2.LINE_AA)
	cv2.putText(spectrum_vertical,calmsg3,(message_loc1,33),font,0.4,(0,255,255),1, cv2.LINE_AA)

	if use_picamera:
		cv2.putText(spectrum_vertical,saveMsg,(message_loc1,51),font,0.4,(0,255,255),1, cv2.LINE_AA)
		cv2.putText(spectrum_vertical,"Gain: "+str(picamGain),(message_loc1,69),font,0.4,(0,255,255),1, cv2.LINE_AA)
	else:
		cv2.putText(spectrum_vertical,"Framerate: "+str(cfps),(message_loc1,51),font,0.4,(0,255,255),1, cv2.LINE_AA)
		cv2.putText(spectrum_vertical,saveMsg,(message_loc1,69),font,0.4,(0,255,255),1, cv2.LINE_AA)

	#Second column
	cv2.putText(spectrum_vertical,holdmsg,(message_loc2,15),font,0.4,(0,255,255),1, cv2.LINE_AA)
	cv2.putText(spectrum_vertical,"Savgol Filter: "+str(savpoly),(message_loc2,33),font,0.4,(0,255,255),1, cv2.LINE_AA)
	cv2.putText(spectrum_vertical,"Label Peak Width: "+str(mindist),(message_loc2,51),font,0.4,(0,255,255),1, cv2.LINE_AA)
	cv2.putText(spectrum_vertical,"Label Threshold: "+str(thresh),(message_loc2,69),font,0.4,(0,255,255),1, cv2.LINE_AA)
	cv2.imshow(title1,spectrum_vertical)

	if dispWaterfall == True:
		#stack the images and display the waterfall	
		waterfall_vertical = np.vstack((messages,cropped, waterfall))
		#dividing lines...
		cv2.line(waterfall_vertical,(0,80),(frameWidth,80),(255,255,255),1)
		cv2.line(waterfall_vertical,(0,160),(frameWidth,160),(255,255,255),1)
		#Draw this stuff over the top of the image!
		#Display a graticule calibrated with cal data
		textoffset = 12

		#vertical lines every whole 50nm
		for positiondata in fifties:
			for i in range(162,480):
				if i%20 == 0:
					cv2.line(waterfall_vertical,(positiondata[0],i),(positiondata[0],i+1),(0,0,0),2)
					cv2.line(waterfall_vertical,(positiondata[0],i),(positiondata[0],i+1),(255,255,255),1)
			cv2.putText(waterfall_vertical,str(positiondata[1])+'nm',(positiondata[0]-textoffset,475),font,0.4,(0,0,0),2, cv2.LINE_AA)
			cv2.putText(waterfall_vertical,str(positiondata[1])+'nm',(positiondata[0]-textoffset,475),font,0.4,(255,255,255),1, cv2.LINE_AA)

		cv2.putText(waterfall_vertical,calmsg1,(message_loc1,15),font,0.4,(0,255,255),1, cv2.LINE_AA)
		
		if use_picamera:
			cv2.putText(waterfall_vertical,calmsg3,(message_loc1,33),font,0.4,(0,255,255),1, cv2.LINE_AA)
			cv2.putText(waterfall_vertical,saveMsg,(message_loc1,51),font,0.4,(0,255,255),1, cv2.LINE_AA)
			cv2.putText(waterfall_vertical,"Gain: "+str(picamGain),(message_loc1,69),font,0.4,(0,255,255),1, cv2.LINE_AA)
		else:
			cv2.putText(waterfall_vertical,calmsg2,(message_loc1,33),font,0.4,(0,255,255),1, cv2.LINE_AA)
			cv2.putText(waterfall_vertical,calmsg3,(message_loc1,51),font,0.4,(0,255,255),1, cv2.LINE_AA)
			cv2.putText(waterfall_vertical,saveMsg,(message_loc1,69),font,0.4,(0,255,255),1, cv2.LINE_AA)
		
		cv2.putText(waterfall_vertical,holdmsg,(message_loc2,15),font,0.4,(0,255,255),1, cv2.LINE_AA)

		cv2.imshow(title2,waterfall_vertical)


	keyPress = cv2.waitKey(1)
	if keyPress == ord('q'):
		break
	elif keyPress == ord('h'):
		if holdpeaks == False:
			holdpeaks = True
		elif holdpeaks == True:
			holdpeaks = False
	elif keyPress == ord("s"):
		#package up the data!
		graphdata = []
		graphdata.append(wavelengthData)
		graphdata.append(intensity)
		if dispWaterfall == True:
			savedata = []
			savedata.append(spectrum_vertical)
			savedata.append(graphdata)
			savedata.append(waterfall_vertical)
		else:
			savedata = []
			savedata.append(spectrum_vertical)
			savedata.append(graphdata)
		saveMsg = snapshot(savedata)
	elif keyPress == ord("c"):
		calcomplete = writecal(clickArray)
		if calcomplete:
			#overwrite wavelength data
			#Go grab the computed calibration data
			caldata = readcal(frameWidth)
			wavelengthData = caldata[0]
			calmsg1 = caldata[1]
			calmsg2 = caldata[2]
			calmsg3 = caldata[3]
			#overwrite graticule data
			graticuleData = generateGraticule(wavelengthData)
			tens = (graticuleData[0])
			fifties = (graticuleData[1])
	elif keyPress == ord("x"):
		clickArray = []
	elif keyPress == ord("m"):
		recPixels = False #turn off recpixels!
		if measure == False:
			measure = True
		elif measure == True:
			measure = False
	elif keyPress == ord("p"):
		measure = False #turn off measure!
		if recPixels == False:
			recPixels = True
		elif recPixels == True:
			recPixels = False
	elif keyPress == ord("o"):#sav up
			savpoly+=1
			if savpoly >=15:
				savpoly=15
	elif keyPress == ord("l"):#sav down
			savpoly-=1
			if savpoly <=0:
				savpoly=0
	elif keyPress == ord("i"):#Peak width up
			mindist+=1
			if mindist >=100:
				mindist=100
	elif keyPress == ord("k"):#Peak Width down
			mindist-=1
			if mindist <=0:
				mindist=0
	elif keyPress == ord("u"):#label thresh up
			thresh+=1
			if thresh >=100:
				thresh=100
	elif keyPress == ord("j"):#label thresh down
			thresh-=1
			if thresh <=0:
				thresh=0
	elif use_picamera and keyPress == ord("t"):#Gain up!
			picamGain += 1
			if picamGain >=50:
				picamGain = 50.0
			picam2.set_controls({"AnalogueGain": picamGain})
			print("Camera Gain: "+str(picamGain))
	elif use_picamera and keyPress == ord("g"):#Gain down
			picamGain -= 1
			if picamGain <=0:
				picamGain = 0.0
			picam2.set_controls({"AnalogueGain": picamGain})
			print("Camera Gain: "+str(picamGain))								
				
#Everything done
if use_device or use_webcam:
	cap.release()
cv2.destroyAllWindows()