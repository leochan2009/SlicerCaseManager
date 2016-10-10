import os
import csv, re, numpy, json, ast, re
import shutil, datetime, logging
import ctk, vtk, qt, slicer
from collections import OrderedDict



from slicer.ScriptedLoadableModule import *
from SlicerProstateUtils.helpers import WatchBoxAttribute, BasicInformationWatchBox, DICOMBasedInformationWatchBox, IncomingDataWindow
from SlicerProstateUtils.mixins import ModuleWidgetMixin, ModuleLogicMixin, ParameterNodeObservationMixin
from SlicerProstateUtils.constants import DICOMTAGS, COLOR, STYLE, FileExtension
from SlicerProstateUtils.events import SlicerProstateEvents

class SlicerCaseManager(ScriptedLoadableModule):
  def __init__(self, parent):
    ScriptedLoadableModule.__init__(self, parent)
    self.parent.title = "SlicerCaseManager"
    self.parent.categories = ["Radiology"]
    self.parent.dependencies = ["SlicerProstate"]
    self.parent.contributors = ["Christian Herz (SPL)","Longquan Chen(SPL)"]
    self.parent.helpText = """A common module for case management in Slicer"""
    self.parent.acknowledgementText = """Surgical Planning Laboratory, Brigham and Women's Hospital, Harvard
                                        Medical School, Boston, USA This work was supported in part by the National
                                        Institutes of Health through grants R01 EB020667, U24 CA180918,
                                        R01 CA111288 and P41 EB015898. The code is originated from the module SliceTracker"""

class SlicerCaseManagerWidget(ModuleWidgetMixin, ScriptedLoadableModuleWidget):
  @property
  def caseRootDir(self):
    return self.casesRootDirectoryButton.directory

  @caseRootDir.setter
  def caseRootDir(self, path):
    try:
      exists = os.path.exists(path)
    except TypeError:
      exists = False
    self.setSetting('CasesRootLocation', path if exists else None)
    self.casesRootDirectoryButton.text = self.truncatePath(path) if exists else "Choose output directory"
    self.casesRootDirectoryButton.toolTip = path
    self.openCaseButton.enabled = exists
    self.createNewCaseButton.enabled = exists
  
  @property
  def caseDirectoryList(self):
    return self._caseDirectoryList

  @caseDirectoryList.setter
  def caseDirectoryList(self,list):
    self._caseDirectoryList = list      
  
  @property
  def preopDataDir(self):
    return self._preopDataDir

  @preopDataDir.setter
  def preopDataDir(self, path):
    self._preopDataDir = path
    if path is None:
      return
    if os.path.exists(path):
      self.loadPreopData()
  
  @property
  def mpReviewPreprocessedOutput(self):
    return os.path.join(self.currentCaseDirectory, "mpReviewPreprocessed") if self.currentCaseDirectory else None

  @property
  def preopDICOMDataDirectory(self):
    return os.path.join(self.currentCaseDirectory, "DICOM", "Preop") if self.currentCaseDirectory else None

  @property
  def intraopDICOMDataDirectory(self):
    return os.path.join(self.currentCaseDirectory, "DICOM", "Intraop") if self.currentCaseDirectory else None

  @property
  def outputDir(self):
    return os.path.join(self.currentCaseDirectory, "SliceTrackerOutputs")

  @property
  def currentCaseDirectory(self):
    return self._currentCaseDirectory

  @property
  def currentTargets(self):
    return self._currentTargets

  @currentTargets.setter
  def currentTargets(self, targets):
    self._currentTargets = targets
    self.targetTableModel.targetList = targets
    if not targets:
      self.targetTableModel.coverProstateTargetList = None
    else:
      coverProstate = self.registrationResults.getMostRecentApprovedCoverProstateRegistration()
      if coverProstate:
        self.targetTableModel.coverProstateTargetList = coverProstate.approvedTargets
    self.targetTable.enabled = targets is not None

  @currentCaseDirectory.setter
  def currentCaseDirectory(self, path):
    self._currentCaseDirectory = path
    valid = path is not None
    self.closeCaseButton.enabled = valid
    if not valid:
      self.caseWatchBox.reset()

  @property
  def generatedOutputDirectory(self):
    return self._generatedOutputDirectory

  @generatedOutputDirectory.setter
  def generatedOutputDirectory(self, path):
    if not os.path.exists(path):
      self.logic.createDirectory(path)
    exists = os.path.exists(path)
    self._generatedOutputDirectory = path if exists else ""
    self.completeCaseButton.enabled = exists and not self.logic.caseCompleted

  def __init__(self, parent=None):
    ScriptedLoadableModuleWidget.__init__(self, parent)
    self.logic = SlicerCaseManagerLogic()
    self.modulePath = os.path.dirname(slicer.util.modulePath(self.moduleName))
    self._currentCaseDirectory = None
    self._caseDirectoryList = {}
    self.caseDirectoryList = {"DICOM/Preop"}

  def setup(self):
    ScriptedLoadableModuleWidget.setup(self)

    self.mainGUIGroupBox = qt.QGroupBox()
    self.mainGUIGroupBoxLayout = qt.QGridLayout()
    self.mainGUIGroupBox.setLayout(self.mainGUIGroupBoxLayout)
    self.createNewCaseButton = self.createButton("New case")
    self.openCaseButton = self.createButton("Open case")
    self.closeCaseButton = self.createButton("Close case", toolTip="Close case without completing it", enabled=False)
    self.completeCaseButton = self.createButton('Case completed', enabled=False)
    self.mainGUIGroupBoxLayout.addWidget(self.createNewCaseButton, 1, 0)
    self.mainGUIGroupBoxLayout.addWidget(self.closeCaseButton, 1, 1)
    self.mainGUIGroupBoxLayout.addWidget(self.completeCaseButton, 1, 1)
    
    self.createPatientWatchBox()
    #self.createIntraopWatchBox()
    self.createCaseInformationArea()
    self.setupConnections()
    self.layout.addWidget(self.mainGUIGroupBox)


  def updateOutputFolder(self):
    if os.path.exists(self.generatedOutputDirectory):
      return
    if self.patientWatchBox.getInformation("PatientID") != '' \
            and self.intraopWatchBox.getInformation("StudyDate") != '':
      if self.outputDir and not os.path.exists(self.outputDir):
        self.logic.createDirectory(self.outputDir)
      finalDirectory = self.patientWatchBox.getInformation("PatientID") + "-biopsy-" + \
                       str(qt.QDate().currentDate()) + "-" + qt.QTime().currentTime().toString().replace(":", "")
      self.generatedOutputDirectory = os.path.join(self.outputDir, finalDirectory, "MRgBiopsy")
    else:
      self.generatedOutputDirectory = ""

  def createPatientWatchBox(self):
    self.patientWatchBoxInformation = [WatchBoxAttribute('PatientID', 'Patient ID: ', DICOMTAGS.PATIENT_ID),
                                       WatchBoxAttribute('PatientName', 'Patient Name: ', DICOMTAGS.PATIENT_NAME),
                                       WatchBoxAttribute('DOB', 'Date of Birth: ', DICOMTAGS.PATIENT_BIRTH_DATE),
                                       WatchBoxAttribute('StudyDate', 'Preop Study Date: ', DICOMTAGS.STUDY_DATE)]
    self.patientWatchBox = DICOMBasedInformationWatchBox(self.patientWatchBoxInformation)
    self.layout.addWidget(self.patientWatchBox)
  
  def createIntraopWatchBox(self):
    intraopWatchBoxInformation = [WatchBoxAttribute('StudyDate', 'Intraop Study Date: ', DICOMTAGS.STUDY_DATE),
                                  WatchBoxAttribute('CurrentSeries', 'Current Series: ', [DICOMTAGS.SERIES_NUMBER,
                                                                                          DICOMTAGS.SERIES_DESCRIPTION])]
    self.intraopWatchBox = DICOMBasedInformationWatchBox(intraopWatchBoxInformation)
    self.registrationDetailsButton = self.createButton("", styleSheet="border:none;",
                                                       maximumWidth=16)
    self.layout.addWidget(self.intraopWatchBox)
  
  def createCaseInformationArea(self):
    self.casesRootDirectoryButton = self.createDirectoryButton(text="Choose cases root location",
                                                               caption="Choose cases root location",
                                                               directory=self.getSetting('CasesRootLocation'))
    self.createCaseWatchBox()
    self.collapsibleDirectoryConfigurationArea = ctk.ctkCollapsibleButton()
    self.collapsibleDirectoryConfigurationArea.collapsed = True
    self.collapsibleDirectoryConfigurationArea.text = "Case Directory Settings"
    self.directoryConfigurationLayout = qt.QGridLayout(self.collapsibleDirectoryConfigurationArea)
    self.directoryConfigurationLayout.addWidget(qt.QLabel("Cases Root Directory"), 1, 0, 1, 1)
    self.directoryConfigurationLayout.addWidget(self.casesRootDirectoryButton, 1, 1, 1, 1)
    self.directoryConfigurationLayout.addWidget(self.caseWatchBox, 2, 0, 1, qt.QSizePolicy.ExpandFlag)
    self.layout.addWidget(self.collapsibleDirectoryConfigurationArea)

  def createCaseWatchBox(self):
    watchBoxInformation = [WatchBoxAttribute('CurrentCaseDirectory', 'Directory')]
    self.caseWatchBox = BasicInformationWatchBox(watchBoxInformation, title="Current Case")


  def setupConnections(self):
    self.createNewCaseButton.clicked.connect(self.onCreateNewCaseButtonClicked)
    self.openCaseButton.clicked.connect(self.onOpenCaseButtonClicked)
    self.casesRootDirectoryButton.directoryChanged.connect(lambda: setattr(self, "caseRootDir",
                                                                           self.casesRootDirectoryButton.directory))
    self.completeCaseButton.clicked.connect(self.onCompleteCaseButtonClicked)
    self.closeCaseButton.clicked.connect(self.clearData)

  def onCreateNewCaseButtonClicked(self):
    if not self.checkAndWarnUserIfCaseInProgress():
      return
    self.clearData()
    self.caseDialog = NewCaseSelectionNameWidget(self.caseRootDir)
    selectedButton = self.caseDialog.exec_()
    if selectedButton == qt.QMessageBox.Ok:
      newCaseDirectory = self.caseDialog.newCaseDirectory
      os.mkdir(newCaseDirectory)
      for direcory in self.caseDirectoryList:
        subDirectory = direcory.split("/")
        for iIndex in range(len(subDirectory)+1):
          fullPath = ""
          for jIndex in range(iIndex):
            fullPath = os.path.join(fullPath,subDirectory[jIndex])
          if not os.path.exists(os.path.join(newCaseDirectory,fullPath)): 
            os.mkdir(os.path.join(newCaseDirectory,fullPath))      
      self.currentCaseDirectory = newCaseDirectory      
      self.startPreopDICOMReceiver()
  
  def onCompleteCaseButtonClicked(self):
    self.logic.caseCompleted = True
    self.save(showDialog=True)
    self.clearData()
  
  def onOpenCaseButtonClicked(self):
    if not self.checkAndWarnUserIfCaseInProgress():
      return
    path = qt.QFileDialog.getExistingDirectory(self.parent.window(), "Select Case Directory", self.caseRootDir)
    if not path:
      return
    self.currentCaseDirectory = path
    if not os.path.exists(os.path.join(path, "DICOM", "Preop")):
      slicer.util.warningDisplay("The selected case directory seems not to be valid", windowTitle="")
      self.clearData()
    else:
      slicer.util.loadVolume(self.preopImagePath, returnNode=True)
      self.loadCaseData()

  def checkAndWarnUserIfCaseInProgress(self):
    proceed = True
    if self.currentCaseDirectory is not None:
      if not slicer.util.confirmYesNoDisplay("Current case will be closed. Do you want to proceed?"):
        proceed = False
    return proceed

  def startPreopDICOMReceiver(self):
    self.preopTransferWindow = IncomingDataWindow(incomingDataDirectory=self.preopDICOMDataDirectory,
                                                  skipText="No Preop available")
    self.preopTransferWindow.addObserver(SlicerProstateEvents.IncomingDataSkippedEvent,
                                         self.continueWithoutPreopData)
    self.preopTransferWindow.addObserver(SlicerProstateEvents.IncomingDataCanceledEvent,
                                         self.onPreopTransferMessageBoxCanceled)
    self.preopTransferWindow.addObserver(SlicerProstateEvents.IncomingDataReceiveFinishedEvent,
                                         self.startPreProcessingPreopData)
    self.preopTransferWindow.show()
  
  def continueWithoutPreopData(self, caller, event):
    self.cleanupPreopDICOMReceiver()
    self.simulatePreopPhaseButton.enabled = False
    self.simulateIntraopPhaseButton.enabled = True
  
  def cleanupPreopDICOMReceiver(self):
    if self.preopTransferWindow:
      self.preopTransferWindow.hide()
      self.preopTransferWindow.removeObservers()
      self.preopTransferWindow = None
  
  def onPreopTransferMessageBoxCanceled(self,caller, event):
    self.clearData()
    pass

  def startPreProcessingPreopData(self, caller=None, event=None):
    self.cleanupPreopDICOMReceiver()
    pass


  def loadCaseData(self):

    pass
  
  def clearData(self):
    pass

class SlicerCaseManagerLogic(ScriptedLoadableModuleLogic):
  
  @property
  def caseCompleted(self):
    return self._caseCompleted

  @caseCompleted.setter
  def caseCompleted(self, value):
    self._caseCompleted = value
    if value is True:
      self.stopSmartDICOMReceiver()
  
  def __init__(self):
    ScriptedLoadableModuleLogic.__init__(self)
    self.caseCompleted = True
    self.DEFAULT_JSON_FILE_NAME = "results.json"
  
  def stopSmartDICOMReceiver(self):
    self.smartDicomReceiver = getattr(self, "smartDicomReceiver", None)
    if self.smartDicomReceiver:
      self.smartDicomReceiver.stop()
      self.smartDicomReceiver.removeObservers()
  
  def closeCase(self, directory):
    self.stopSmartDICOMReceiver()
    if os.path.exists(directory):
      self.caseCompleted = False
      if self.getDirectorySize(directory) == 0:
        shutil.rmtree(directory)
        
  def hasCaseBeenCompleted(self, directory):
    self.caseCompleted = False
    filename = os.path.join(directory, self.DEFAULT_JSON_FILE_NAME)
    if not os.path.exists(filename):
      return
    with open(filename) as data_file:
      data = json.load(data_file)
      self.caseCompleted = data["completed"]
    return self.caseCompleted    

class SliceTrackerCaseManager(ScriptedLoadableModule):
  def __init__(self, parent):
    ScriptedLoadableModule.__init__(self, parent)
    self.parent.title = "SliceTrackerCaseManager"
    self.parent.categories = ["Radiology"]
    self.parent.dependencies = ["SlicerProstate"]
    self.parent.contributors = ["Christian Herz (SPL)","Longquan Chen(SPL)"]
    self.parent.helpText = """A common module for case management in Slicer"""
    self.parent.acknowledgementText = """Surgical Planning Laboratory, Brigham and Women's Hospital, Harvard
                                        Medical School, Boston, USA This work was supported in part by the National
                                        Institutes of Health through grants R01 EB020667, U24 CA180918,
                                        R01 CA111288 and P41 EB015898. The code is originated from the module SliceTracker"""


class SliceTrackerCaseManagerWidget(SlicerCaseManagerWidget):
  def __init__(self, parent=None):
    ScriptedLoadableModuleWidget.__init__(self, parent)
    self.logic = SliceTrackerCaseManagerLogic()
    
  def setup(self):
    SlicerCaseManagerWidget.setup(self)  
    self.caseDirectoryList = {""}
  
  def onCreateNewCaseButtonClicked(self):
    Super(SliceTrackerCaseManagerWidget,self).onCreateNewCaseButtonClicked()
    self.simulatePreopPhaseButton.enabled = True
    self.simulateIntraopPhaseButton.enabled = True
    
  def setupTrainingSectionUIElements(self):
    self.collapsibleTrainingArea = ctk.ctkCollapsibleButton()
    self.collapsibleTrainingArea.collapsed = True
    self.collapsibleTrainingArea.text = "Training"

    self.simulatePreopPhaseButton = self.createButton("Simulate preop phase", enabled=False)
    self.simulateIntraopPhaseButton = self.createButton("Simulate intraop phase", enabled=False)

    self.trainingsAreaLayout = qt.QGridLayout(self.collapsibleTrainingArea)
    self.trainingsAreaLayout.addWidget(self.createHLayout([self.simulatePreopPhaseButton,
                                                           self.simulateIntraopPhaseButton]))  
  
  def loadCaseData(self):
    Super(SliceTrackerCaseManagerWidget, self).loadCaseData()
    from mpReview import mpReviewLogic
    savedSessions = self.logic.getSavedSessions(self.currentCaseDirectory)
    if len(savedSessions) > 0: # After registration(s) has been done
      if not self.openSavedSession(savedSessions):
        self.clearData()
    else:
      if os.path.exists(self.mpReviewPreprocessedOutput) and \
              mpReviewLogic.wasmpReviewPreprocessed(self.mpReviewPreprocessedOutput):
        self.preopDataDir = self.logic.getFirstMpReviewPreprocessedStudy(self.mpReviewPreprocessedOutput)
        self.intraopDataDir = self.intraopDICOMDataDirectory
      else:
        if len(os.listdir(self.preopDICOMDataDirectory)):
          self.startPreProcessingPreopData()
        elif len(os.listdir(self.intraopDICOMDataDirectory)):
          self.logic.usePreopData = False
          self.intraopDataDir = self.intraopDICOMDataDirectory
        else:
          self.startPreopDICOMReceiver()
    self.configureAllTargetDisplayNodes()
    
  def openSavedSession(self, sessions):
    # TODO: session selector and if not continuing, ask for creating a new one.
    latestCase = os.path.join(max(sessions, key=os.path.getmtime), "MRgBiopsy")
    self.logic.caseCompleted = self.logic.hasCaseBeenCompleted(latestCase)
    message = "A %s session has been found for the selected case. Do you want to %s?" \
              % ("completed" if self.logic.caseCompleted else "started",
                 "open it" if self.logic.caseCompleted else "continue this session")
    if slicer.util.confirmYesNoDisplay(message):
      self.continueOldCase = True
      self.logic.loadFromJSON(latestCase)
      if self.logic.usePreopData:
        self.preopDataDir = self.logic.getFirstMpReviewPreprocessedStudy(self.mpReviewPreprocessedOutput)
      else:
        if self.logic.preopTargets:
          self.setupPreopLoadedTargets()
      self.generatedOutputDirectory = latestCase
      self.intraopDataDir = os.path.join(self.currentCaseDirectory, "DICOM", "Intraop")
      return True
    else:
      return False
    
  def clearData(self):
    Super(SliceTrackerCaseManagerWidget, self).clearData()
    self.simulatePreopPhaseButton.enabled = False
    self.simulateIntraopPhaseButton.enabled = False
    self.cleanupPreopDICOMReceiver()
    self.sampleDownloader.resetAndInitialize()
    if self.caseManagerWidget.currentCaseDirectory:
      self.logic.closeCase(self.caseManagerWidget.currentCaseDirectory)
      self.caseManagerWidget.currentCaseDirectory = None
    slicer.mrmlScene.Clear(0)
    self.logic.resetAndInitializeData()
    self.updateIntraopSeriesSelectorTable()
    self.updateIntraopSeriesSelectorColor(None)
    self.removeSliceAnnotations()
    self.seriesModel.clear()
    self.trackTargetsButton.setEnabled(False)
    self.currentTargets = None
    self.resetViewSettingButtons()
    self.resetVisualEffects()
    self.disconnectKeyEventObservers()
    self.patientWatchBox.sourceFile = None
    self.intraopWatchBox.sourceFile = None
    self.continueOldCase = False
    if self.customStatusProgressBar:
      self.customStatusProgressBar.reset()
      self.customStatusProgressBar.hide()   
      
  def continueWithoutPreopData(self, caller, event):
    Super(SliceTrackerCaseManagerWidget, self).continueWithoutPreopData(caller, event)
    self.logic.usePreopData = False
    self.simulatePreopPhaseButton.enabled = False
    self.simulateIntraopPhaseButton.enabled = True    

class SliceTrackerCaseManagerLogic(SlicerCaseManagerLogic):
  def __init__(self):
    ScriptedLoadableModuleLogic.__init__(self)
    self.seriesList = []
    self.loadableList = {}
    
  @property
  def intraopDataDir(self):
    return self._intraopDataDir

  @intraopDataDir.setter
  def intraopDataDir(self, path):
    self._intraopDataDir = path
    if not self.caseCompleted:
      self.startSmartDICOMReceiver(runStoreSCP=not self.trainingMode)
    else:
      self.invokeEvent(SlicerProstateEvents.DICOMReceiverStoppedEvent)
    self.importDICOMSeries(self.getFileList(self.intraopDataDir))
    if self.smartDicomReceiver:
      self.smartDicomReceiver.forceStatusChangeEvent()
      
  def startSmartDICOMReceiver(self, runStoreSCP=True):
    self.stopSmartDICOMReceiver()
    self.smartDicomReceiver = SmartDICOMReceiver(self.intraopDataDir)
    self.smartDicomReceiver.addObserver(SlicerProstateEvents.IncomingDataReceiveFinishedEvent,
                                        self.onDICOMSeriesReceived)
    self.smartDicomReceiver.addObserver(SlicerProstateEvents.StatusChangedEvent,
                                        self.onDICOMReceiverStatusChanged)
    self.smartDicomReceiver.addObserver(SlicerProstateEvents.DICOMReceiverStoppedEvent,
                                        self.onSmartDICOMReceiverStopped)
    self.smartDicomReceiver.start(runStoreSCP)

  def onSmartDICOMReceiverStopped(self, caller, event, callData=None):
    self.invokeEvent(SlicerProstateEvents.DICOMReceiverStoppedEvent)

  @vtk.calldata_type(vtk.VTK_STRING)
  def onDICOMReceiverStatusChanged(self, caller, event, callData):
    self.invokeEvent(SlicerProstateEvents.StatusChangedEvent, callData)

  @vtk.calldata_type(vtk.VTK_STRING)
  def onDICOMSeriesReceived(self, caller, event, callData):
    newFileList = ast.literal_eval(callData)
    self.importDICOMSeries(newFileList)
    if self.trainingMode is True:
      self.stopSmartDICOMReceiver()

  def importDICOMSeries(self, newFileList):
    indexer = ctk.ctkDICOMIndexer()

    eligibleSeriesFiles = []
    size = len(newFileList)
    for currentIndex, currentFile in enumerate(newFileList, start=1):
      self.invokeEvent(SlicerProstateEvents.NewFileIndexedEvent, ["Indexing file %s" % currentFile, size, currentIndex].__str__())
      slicer.app.processEvents()
      currentFile = os.path.join(self._intraopDataDir, currentFile)
      indexer.addFile(slicer.dicomDatabase, currentFile, None)
      series = self.makeSeriesNumberDescription(currentFile)
      if series:
        eligibleSeriesFiles.append(currentFile)
        if series not in self.seriesList:
          self.seriesList.append(series)
          self.createLoadableFileListForSeries(series)

    self.seriesList = sorted(self.seriesList, key=lambda s: RegistrationResult.getSeriesNumberFromString(s))

    if len(eligibleSeriesFiles):
      self.invokeEvent(SlicerProstateEvents.NewImageDataReceivedEvent, eligibleSeriesFiles.__str__())

  def createLoadableFileListForSeries(self, selectedSeries):
    selectedSeriesNumber = int(selectedSeries.split(": ")[0])
    self.loadableList[selectedSeries] = []
    for dcm in self.getFileList(self._intraopDataDir):
      currentFile = os.path.join(self._intraopDataDir, dcm)
      currentSeriesNumber = int(self.getDICOMValue(currentFile, DICOMTAGS.SERIES_NUMBER))
      if currentSeriesNumber and currentSeriesNumber == selectedSeriesNumber:
        self.loadableList[selectedSeries].append(currentFile)    
    
class NewCaseSelectionNameWidget(qt.QMessageBox, ModuleWidgetMixin):

  PREFIX = "Case"
  SUFFIX = "-" + datetime.date.today().strftime("%Y%m%d")
  SUFFIX_PATTERN = "-[0-9]{8}"
  CASE_NUMBER_DIGITS = 3
  PATTERN = PREFIX+"[0-9]{"+str(CASE_NUMBER_DIGITS-1)+"}[0-9]{1}"+SUFFIX_PATTERN

  def __init__(self, destination, parent=None):
    super(NewCaseSelectionNameWidget, self).__init__(parent)
    if not os.path.exists(destination):
      raise
    self.destinationRoot = destination
    self.newCaseDirectory = None
    self.minimum = self.getNextCaseNumber()
    self.setupUI()
    self.setupConnections()
    self.onCaseNumberChanged(self.minimum)

  def getNextCaseNumber(self):
    import re
    caseNumber = 0
    for dirName in [dirName for dirName in os.listdir(self.destinationRoot)
                     if os.path.isdir(os.path.join(self.destinationRoot, dirName)) and re.match(self.PATTERN, dirName)]:
      number = int(re.split(self.SUFFIX_PATTERN, dirName)[0].split(self.PREFIX)[1])
      caseNumber = caseNumber if caseNumber > number else number
    return caseNumber+1

  def setupUI(self):
    self.setWindowTitle("Case Number Selection")
    self.setText("Please select a case number for the new case.")
    self.setIcon(qt.QMessageBox.Question)
    self.spinbox = qt.QSpinBox()
    self.spinbox.setRange(self.minimum, int("9"*self.CASE_NUMBER_DIGITS))
    self.preview = qt.QLabel()
    self.notice = qt.QLabel()
    self.layout().addWidget(self.createVLayout([self.createHLayout([qt.QLabel("Proposed Case Number"), self.spinbox]),
                                                self.preview, self.notice]), 2, 1)
    self.okButton = self.addButton(self.Ok)
    self.okButton.enabled = False
    self.cancelButton = self.addButton(self.Cancel)
    self.setDefaultButton(self.okButton)

  def setupConnections(self):
    self.spinbox.valueChanged.connect(self.onCaseNumberChanged)

  def onCaseNumberChanged(self, caseNumber):
    formatString = '%0'+str(self.CASE_NUMBER_DIGITS)+'d'
    caseNumber = formatString % caseNumber
    directory = self.PREFIX+caseNumber+self.SUFFIX
    self.newCaseDirectory = os.path.join(self.destinationRoot, directory)
    self.preview.setText("New case directory: " + self.newCaseDirectory)
    self.okButton.enabled = not os.path.exists(self.newCaseDirectory)
    self.notice.text = "" if not os.path.exists(self.newCaseDirectory) else "Note: Directory already exists."