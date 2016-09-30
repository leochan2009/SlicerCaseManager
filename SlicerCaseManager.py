import os
import csv, re, numpy, json, ast, re
import shutil, datetime, logging
import ctk, vtk, qt
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
    self.parent.contributors = ["Longquan Chen(SPL)","Christian Herz (SPL)"]
    self.parent.helpText = """A common module for case management in Slicer"""
    self.parent.acknowledgementText = """Surgical Planning Laboratory, Brigham and Women's Hospital, Harvard
                                        Medical School, Boston, USA This work was supported in part by the National
                                        Institutes of Health through grants R01 EB020667, U24 CA180918,
                                        R01 CA111288 and P41 EB015898. The code is originated from the module SliceTracker"""

class SlicerCaseManagerWidget(ModuleWidgetMixin, ScriptedLoadableModuleWidget):
  @property
  def currentCaseDirectory(self):
    return self._currentCaseDirectory

  @currentCaseDirectory.setter
  def currentCaseDirectory(self, path):
    self._currentCaseDirectory = path
    valid = path is not None
    if valid:
      value = self.currentCaseDirectory
      self.caseWatchBox.setInformation("CurrentCaseDirectory", os.path.relpath(value, self.caseRootDir), toolTip=value)
    else:
      self.caseWatchBox.reset()

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
  def preopDICOMDataDirectory(self):
    return os.path.join(self.currentCaseDirectory, "DICOM", "Preop") if self.currentCaseDirectory else None

  def __init__(self, parent=None):
    ScriptedLoadableModuleWidget.__init__(self, parent)
    self.modulePath = os.path.dirname(slicer.util.modulePath(self.moduleName))
    self._currentCaseDirectory = None

  def setup(self):
    ScriptedLoadableModuleWidget.setup(self)

    self.mainGUIGroupBox = qt.QGroupBox()
    self.mainGUIGroupBoxLayout = qt.QGridLayout()
    self.mainGUIGroupBox.setLayout(self.mainGUIGroupBoxLayout)
    self.createNewCaseButton = self.createButton("New case")
    self.openCaseButton = self.createButton("Open case")
    self.mainGUIGroupBoxLayout.addWidget(self.createNewCaseButton, 1, 0)
    self.mainGUIGroupBoxLayout.addWidget(self.openCaseButton, 1, 1)
    self.createPatientWatchBox()
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

  def onCreateNewCaseButtonClicked(self):
    if not self.checkAndWarnUserIfCaseInProgress():
      return
    self.caseDialog = NewCaseSelectionNameWidget(self.caseRootDir)
    selectedButton = self.caseDialog.exec_()
    if selectedButton == qt.QMessageBox.Ok:
      newCaseDirectory = self.caseDialog.newCaseDirectory
      os.mkdir(newCaseDirectory)
      os.mkdir(os.path.join(newCaseDirectory, "DICOM"))
      os.mkdir(os.path.join(newCaseDirectory, "DICOM", "Preop"))
      os.mkdir(os.path.join(newCaseDirectory, "VentriculostomyOutputs"))
      self.startPreopDICOMReceiver()

  def onOpenCaseButtonClicked(self):
    if not self.checkAndWarnUserIfCaseInProgress():
      return
    path = qt.QFileDialog.getExistingDirectory(self.parent.window(), "Select Case Directory", self.caseRootDir)
    if not path:
      return
    self.currentCaseDirectory = path
    if not os.path.exists(os.path.join(path, "DICOM", "Preop")):
      slicer.util.warningDisplay("The selected case directory seems not to be valid", windowTitle="")
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
    self.preopTransferWindow.addObserver(SlicerProstateEvents.IncomingDataCanceledEvent,
                                         self.onPreopTransferMessageBoxCanceled)
    self.preopTransferWindow.addObserver(SlicerProstateEvents.IncomingDataReceiveFinishedEvent,
                                         self.startPreProcessingPreopData)
    self.preopTransferWindow.show()

  def onPreopTransferMessageBoxCanceled(self,caller, event):
    pass

  def startPreProcessingPreopData(self, caller, event):
    pass


  def loadCaseData(self):

    pass

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