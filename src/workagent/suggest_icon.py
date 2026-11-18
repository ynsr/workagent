"""AI-suggested Lucide icons (issue #62; prompt improved in #84): shared prompt + parsing + validation.

Runs ONE bare judge-style harness session (cheap model, no skills/tools/
rules/context — ``harness.judge_argv``) in a temp dir (no repo worktree
needed), parses the model answer down to a single icon name, and validates
it against the full Lucide list for the pinned ``lucide-react`` version
(``ICONS_LUCIDE_VERSION`` below; names from that package's ``icons``
registry). The frontend picker (``web/src/components/IconPicker.tsx``
``ACTION_ICONS``) stays a small curated subset for fast browsing, but the
suggestion contract accepts any name in ``SUGGEST_ICON_CHOICES``.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

#: Pinned lucide-react version the icon list below was extracted from.
ICONS_LUCIDE_VERSION = "0.525.0"

#: Full Lucide icon list for the pinned version (from that package's `icons`
#: registry keys — every name renders via ActionIcon/IconPicker).
SUGGEST_ICON_CHOICES = (
    'AArrowDown', 'AArrowUp', 'ALargeSmall', 'Accessibility', 'Activity', 'AirVent', 'Airplay', 'AlarmClock',
    'AlarmClockCheck', 'AlarmClockMinus', 'AlarmClockOff', 'AlarmClockPlus', 'AlarmSmoke', 'Album', 'AlignCenter', 'AlignCenterHorizontal',
    'AlignCenterVertical', 'AlignEndHorizontal', 'AlignEndVertical', 'AlignHorizontalDistributeCenter', 'AlignHorizontalDistributeEnd', 'AlignHorizontalDistributeStart', 'AlignHorizontalJustifyCenter', 'AlignHorizontalJustifyEnd',
    'AlignHorizontalJustifyStart', 'AlignHorizontalSpaceAround', 'AlignHorizontalSpaceBetween', 'AlignJustify', 'AlignLeft', 'AlignRight', 'AlignStartHorizontal', 'AlignStartVertical',
    'AlignVerticalDistributeCenter', 'AlignVerticalDistributeEnd', 'AlignVerticalDistributeStart', 'AlignVerticalJustifyCenter', 'AlignVerticalJustifyEnd', 'AlignVerticalJustifyStart', 'AlignVerticalSpaceAround', 'AlignVerticalSpaceBetween',
    'Ambulance', 'Ampersand', 'Ampersands', 'Amphora', 'Anchor', 'Angry', 'Annoyed', 'Antenna',
    'Anvil', 'Aperture', 'AppWindow', 'AppWindowMac', 'Apple', 'Archive', 'ArchiveRestore', 'ArchiveX',
    'Armchair', 'ArrowBigDown', 'ArrowBigDownDash', 'ArrowBigLeft', 'ArrowBigLeftDash', 'ArrowBigRight', 'ArrowBigRightDash', 'ArrowBigUp',
    'ArrowBigUpDash', 'ArrowDown', 'ArrowDown01', 'ArrowDown10', 'ArrowDownAZ', 'ArrowDownFromLine', 'ArrowDownLeft', 'ArrowDownNarrowWide',
    'ArrowDownRight', 'ArrowDownToDot', 'ArrowDownToLine', 'ArrowDownUp', 'ArrowDownWideNarrow', 'ArrowDownZA', 'ArrowLeft', 'ArrowLeftFromLine',
    'ArrowLeftRight', 'ArrowLeftToLine', 'ArrowRight', 'ArrowRightFromLine', 'ArrowRightLeft', 'ArrowRightToLine', 'ArrowUp', 'ArrowUp01',
    'ArrowUp10', 'ArrowUpAZ', 'ArrowUpDown', 'ArrowUpFromDot', 'ArrowUpFromLine', 'ArrowUpLeft', 'ArrowUpNarrowWide', 'ArrowUpRight',
    'ArrowUpToLine', 'ArrowUpWideNarrow', 'ArrowUpZA', 'ArrowsUpFromLine', 'Asterisk', 'AtSign', 'Atom', 'AudioLines',
    'AudioWaveform', 'Award', 'Axe', 'Axis3d', 'Baby', 'Backpack', 'Badge', 'BadgeAlert',
    'BadgeCent', 'BadgeCheck', 'BadgeDollarSign', 'BadgeEuro', 'BadgeIndianRupee', 'BadgeInfo', 'BadgeJapaneseYen', 'BadgeMinus',
    'BadgePercent', 'BadgePlus', 'BadgePoundSterling', 'BadgeQuestionMark', 'BadgeRussianRuble', 'BadgeSwissFranc', 'BadgeX', 'BaggageClaim',
    'Ban', 'Banana', 'Bandage', 'Banknote', 'BanknoteArrowDown', 'BanknoteArrowUp', 'BanknoteX', 'Barcode',
    'Barrel', 'Baseline', 'Bath', 'Battery', 'BatteryCharging', 'BatteryFull', 'BatteryLow', 'BatteryMedium',
    'BatteryPlus', 'BatteryWarning', 'Beaker', 'Bean', 'BeanOff', 'Bed', 'BedDouble', 'BedSingle',
    'Beef', 'Beer', 'BeerOff', 'Bell', 'BellDot', 'BellElectric', 'BellMinus', 'BellOff',
    'BellPlus', 'BellRing', 'BetweenHorizontalEnd', 'BetweenHorizontalStart', 'BetweenVerticalEnd', 'BetweenVerticalStart', 'BicepsFlexed', 'Bike',
    'Binary', 'Binoculars', 'Biohazard', 'Bird', 'Bitcoin', 'Blend', 'Blinds', 'Blocks',
    'Bluetooth', 'BluetoothConnected', 'BluetoothOff', 'BluetoothSearching', 'Bold', 'Bolt', 'Bomb', 'Bone',
    'Book', 'BookA', 'BookAlert', 'BookAudio', 'BookCheck', 'BookCopy', 'BookDashed', 'BookDown',
    'BookHeadphones', 'BookHeart', 'BookImage', 'BookKey', 'BookLock', 'BookMarked', 'BookMinus', 'BookOpen',
    'BookOpenCheck', 'BookOpenText', 'BookPlus', 'BookText', 'BookType', 'BookUp', 'BookUp2', 'BookUser',
    'BookX', 'Bookmark', 'BookmarkCheck', 'BookmarkMinus', 'BookmarkPlus', 'BookmarkX', 'BoomBox', 'Bot',
    'BotMessageSquare', 'BotOff', 'BottleWine', 'BowArrow', 'Box', 'Boxes', 'Braces', 'Brackets',
    'Brain', 'BrainCircuit', 'BrainCog', 'BrickWall', 'BrickWallFire', 'Briefcase', 'BriefcaseBusiness', 'BriefcaseConveyorBelt',
    'BriefcaseMedical', 'BringToFront', 'Brush', 'BrushCleaning', 'Bubbles', 'Bug', 'BugOff', 'BugPlay',
    'Building', 'Building2', 'Bus', 'BusFront', 'Cable', 'CableCar', 'Cake', 'CakeSlice',
    'Calculator', 'Calendar', 'Calendar1', 'CalendarArrowDown', 'CalendarArrowUp', 'CalendarCheck', 'CalendarCheck2', 'CalendarClock',
    'CalendarCog', 'CalendarDays', 'CalendarFold', 'CalendarHeart', 'CalendarMinus', 'CalendarMinus2', 'CalendarOff', 'CalendarPlus',
    'CalendarPlus2', 'CalendarRange', 'CalendarSearch', 'CalendarSync', 'CalendarX', 'CalendarX2', 'Camera', 'CameraOff',
    'Candy', 'CandyCane', 'CandyOff', 'Cannabis', 'Captions', 'CaptionsOff', 'Car', 'CarFront',
    'CarTaxiFront', 'Caravan', 'CardSim', 'Carrot', 'CaseLower', 'CaseSensitive', 'CaseUpper', 'CassetteTape',
    'Cast', 'Castle', 'Cat', 'Cctv', 'ChartArea', 'ChartBar', 'ChartBarBig', 'ChartBarDecreasing',
    'ChartBarIncreasing', 'ChartBarStacked', 'ChartCandlestick', 'ChartColumn', 'ChartColumnBig', 'ChartColumnDecreasing', 'ChartColumnIncreasing', 'ChartColumnStacked',
    'ChartGantt', 'ChartLine', 'ChartNetwork', 'ChartNoAxesColumn', 'ChartNoAxesColumnDecreasing', 'ChartNoAxesColumnIncreasing', 'ChartNoAxesCombined', 'ChartNoAxesGantt',
    'ChartPie', 'ChartScatter', 'ChartSpline', 'Check', 'CheckCheck', 'CheckLine', 'ChefHat', 'Cherry',
    'ChevronDown', 'ChevronFirst', 'ChevronLast', 'ChevronLeft', 'ChevronRight', 'ChevronUp', 'ChevronsDown', 'ChevronsDownUp',
    'ChevronsLeft', 'ChevronsLeftRight', 'ChevronsLeftRightEllipsis', 'ChevronsRight', 'ChevronsRightLeft', 'ChevronsUp', 'ChevronsUpDown', 'Chrome',
    'Church', 'Cigarette', 'CigaretteOff', 'Circle', 'CircleAlert', 'CircleArrowDown', 'CircleArrowLeft', 'CircleArrowOutDownLeft',
    'CircleArrowOutDownRight', 'CircleArrowOutUpLeft', 'CircleArrowOutUpRight', 'CircleArrowRight', 'CircleArrowUp', 'CircleCheck', 'CircleCheckBig', 'CircleChevronDown',
    'CircleChevronLeft', 'CircleChevronRight', 'CircleChevronUp', 'CircleDashed', 'CircleDivide', 'CircleDollarSign', 'CircleDot', 'CircleDotDashed',
    'CircleEllipsis', 'CircleEqual', 'CircleFadingArrowUp', 'CircleFadingPlus', 'CircleGauge', 'CircleMinus', 'CircleOff', 'CircleParking',
    'CircleParkingOff', 'CirclePause', 'CirclePercent', 'CirclePlay', 'CirclePlus', 'CirclePoundSterling', 'CirclePower', 'CircleQuestionMark',
    'CircleSlash', 'CircleSlash2', 'CircleSmall', 'CircleStop', 'CircleUser', 'CircleUserRound', 'CircleX', 'CircuitBoard',
    'Citrus', 'Clapperboard', 'Clipboard', 'ClipboardCheck', 'ClipboardCopy', 'ClipboardList', 'ClipboardMinus', 'ClipboardPaste',
    'ClipboardPen', 'ClipboardPenLine', 'ClipboardPlus', 'ClipboardType', 'ClipboardX', 'Clock', 'Clock1', 'Clock10',
    'Clock11', 'Clock12', 'Clock2', 'Clock3', 'Clock4', 'Clock5', 'Clock6', 'Clock7',
    'Clock8', 'Clock9', 'ClockAlert', 'ClockArrowDown', 'ClockArrowUp', 'ClockFading', 'ClockPlus', 'Cloud',
    'CloudAlert', 'CloudCheck', 'CloudCog', 'CloudDownload', 'CloudDrizzle', 'CloudFog', 'CloudHail', 'CloudLightning',
    'CloudMoon', 'CloudMoonRain', 'CloudOff', 'CloudRain', 'CloudRainWind', 'CloudSnow', 'CloudSun', 'CloudSunRain',
    'CloudUpload', 'Cloudy', 'Clover', 'Club', 'Code', 'CodeXml', 'Codepen', 'Codesandbox',
    'Coffee', 'Cog', 'Coins', 'Columns2', 'Columns3', 'Columns3Cog', 'Columns4', 'Combine',
    'Command', 'Compass', 'Component', 'Computer', 'ConciergeBell', 'Cone', 'Construction', 'Contact',
    'ContactRound', 'Container', 'Contrast', 'Cookie', 'CookingPot', 'Copy', 'CopyCheck', 'CopyMinus',
    'CopyPlus', 'CopySlash', 'CopyX', 'Copyleft', 'Copyright', 'CornerDownLeft', 'CornerDownRight', 'CornerLeftDown',
    'CornerLeftUp', 'CornerRightDown', 'CornerRightUp', 'CornerUpLeft', 'CornerUpRight', 'Cpu', 'CreativeCommons', 'CreditCard',
    'Croissant', 'Crop', 'Cross', 'Crosshair', 'Crown', 'Cuboid', 'CupSoda', 'Currency',
    'Cylinder', 'Dam', 'Database', 'DatabaseBackup', 'DatabaseZap', 'DecimalsArrowLeft', 'DecimalsArrowRight', 'Delete',
    'Dessert', 'Diameter', 'Diamond', 'DiamondMinus', 'DiamondPercent', 'DiamondPlus', 'Dice1', 'Dice2',
    'Dice3', 'Dice4', 'Dice5', 'Dice6', 'Dices', 'Diff', 'Disc', 'Disc2',
    'Disc3', 'DiscAlbum', 'Divide', 'Dna', 'DnaOff', 'Dock', 'Dog', 'DollarSign',
    'Donut', 'DoorClosed', 'DoorClosedLocked', 'DoorOpen', 'Dot', 'Download', 'DraftingCompass', 'Drama',
    'Dribbble', 'Drill', 'Drone', 'Droplet', 'DropletOff', 'Droplets', 'Drum', 'Drumstick',
    'Dumbbell', 'Ear', 'EarOff', 'Earth', 'EarthLock', 'Eclipse', 'Egg', 'EggFried',
    'EggOff', 'Ellipsis', 'EllipsisVertical', 'Equal', 'EqualApproximately', 'EqualNot', 'Eraser', 'EthernetPort',
    'Euro', 'Expand', 'ExternalLink', 'Eye', 'EyeClosed', 'EyeOff', 'Facebook', 'Factory',
    'Fan', 'FastForward', 'Feather', 'Fence', 'FerrisWheel', 'Figma', 'File', 'FileArchive',
    'FileAudio', 'FileAudio2', 'FileAxis3d', 'FileBadge', 'FileBadge2', 'FileBox', 'FileChartColumn', 'FileChartColumnIncreasing',
    'FileChartLine', 'FileChartPie', 'FileCheck', 'FileCheck2', 'FileClock', 'FileCode', 'FileCode2', 'FileCog',
    'FileDiff', 'FileDigit', 'FileDown', 'FileHeart', 'FileImage', 'FileInput', 'FileJson', 'FileJson2',
    'FileKey', 'FileKey2', 'FileLock', 'FileLock2', 'FileMinus', 'FileMinus2', 'FileMusic', 'FileOutput',
    'FilePen', 'FilePenLine', 'FilePlus', 'FilePlus2', 'FileQuestionMark', 'FileScan', 'FileSearch', 'FileSearch2',
    'FileSliders', 'FileSpreadsheet', 'FileStack', 'FileSymlink', 'FileTerminal', 'FileText', 'FileType', 'FileType2',
    'FileUp', 'FileUser', 'FileVideo', 'FileVideo2', 'FileVolume', 'FileVolume2', 'FileWarning', 'FileX',
    'FileX2', 'Files', 'Film', 'Fingerprint', 'FireExtinguisher', 'Fish', 'FishOff', 'FishSymbol',
    'Flag', 'FlagOff', 'FlagTriangleLeft', 'FlagTriangleRight', 'Flame', 'FlameKindling', 'Flashlight', 'FlashlightOff',
    'FlaskConical', 'FlaskConicalOff', 'FlaskRound', 'FlipHorizontal', 'FlipHorizontal2', 'FlipVertical', 'FlipVertical2', 'Flower',
    'Flower2', 'Focus', 'FoldHorizontal', 'FoldVertical', 'Folder', 'FolderArchive', 'FolderCheck', 'FolderClock',
    'FolderClosed', 'FolderCode', 'FolderCog', 'FolderDot', 'FolderDown', 'FolderGit', 'FolderGit2', 'FolderHeart',
    'FolderInput', 'FolderKanban', 'FolderKey', 'FolderLock', 'FolderMinus', 'FolderOpen', 'FolderOpenDot', 'FolderOutput',
    'FolderPen', 'FolderPlus', 'FolderRoot', 'FolderSearch', 'FolderSearch2', 'FolderSymlink', 'FolderSync', 'FolderTree',
    'FolderUp', 'FolderX', 'Folders', 'Footprints', 'Forklift', 'Forward', 'Frame', 'Framer',
    'Frown', 'Fuel', 'Fullscreen', 'Funnel', 'FunnelPlus', 'FunnelX', 'GalleryHorizontal', 'GalleryHorizontalEnd',
    'GalleryThumbnails', 'GalleryVertical', 'GalleryVerticalEnd', 'Gamepad', 'Gamepad2', 'Gauge', 'Gavel', 'Gem',
    'GeorgianLari', 'Ghost', 'Gift', 'GitBranch', 'GitBranchPlus', 'GitCommitHorizontal', 'GitCommitVertical', 'GitCompare',
    'GitCompareArrows', 'GitFork', 'GitGraph', 'GitMerge', 'GitPullRequest', 'GitPullRequestArrow', 'GitPullRequestClosed', 'GitPullRequestCreate',
    'GitPullRequestCreateArrow', 'GitPullRequestDraft', 'Github', 'Gitlab', 'GlassWater', 'Glasses', 'Globe', 'GlobeLock',
    'Goal', 'Gpu', 'Grab', 'GraduationCap', 'Grape', 'Grid2x2', 'Grid2x2Check', 'Grid2x2Plus',
    'Grid2x2X', 'Grid3x2', 'Grid3x3', 'Grip', 'GripHorizontal', 'GripVertical', 'Group', 'Guitar',
    'Ham', 'Hamburger', 'Hammer', 'Hand', 'HandCoins', 'HandHeart', 'HandHelping', 'HandMetal',
    'HandPlatter', 'Handshake', 'HardDrive', 'HardDriveDownload', 'HardDriveUpload', 'HardHat', 'Hash', 'Haze',
    'HdmiPort', 'Heading', 'Heading1', 'Heading2', 'Heading3', 'Heading4', 'Heading5', 'Heading6',
    'HeadphoneOff', 'Headphones', 'Headset', 'Heart', 'HeartCrack', 'HeartHandshake', 'HeartMinus', 'HeartOff',
    'HeartPlus', 'HeartPulse', 'Heater', 'Hexagon', 'Highlighter', 'History', 'Hop', 'HopOff',
    'Hospital', 'Hotel', 'Hourglass', 'House', 'HousePlug', 'HousePlus', 'HouseWifi', 'IceCreamBowl',
    'IceCreamCone', 'IdCard', 'IdCardLanyard', 'Image', 'ImageDown', 'ImageMinus', 'ImageOff', 'ImagePlay',
    'ImagePlus', 'ImageUp', 'ImageUpscale', 'Images', 'Import', 'Inbox', 'IndentDecrease', 'IndentIncrease',
    'IndianRupee', 'Infinity', 'Info', 'InspectionPanel', 'Instagram', 'Italic', 'IterationCcw', 'IterationCw',
    'JapaneseYen', 'Joystick', 'Kanban', 'Key', 'KeyRound', 'KeySquare', 'Keyboard', 'KeyboardMusic',
    'KeyboardOff', 'Lamp', 'LampCeiling', 'LampDesk', 'LampFloor', 'LampWallDown', 'LampWallUp', 'LandPlot',
    'Landmark', 'Languages', 'Laptop', 'LaptopMinimal', 'LaptopMinimalCheck', 'Lasso', 'LassoSelect', 'Laugh',
    'Layers', 'Layers2', 'LayoutDashboard', 'LayoutGrid', 'LayoutList', 'LayoutPanelLeft', 'LayoutPanelTop', 'LayoutTemplate',
    'Leaf', 'LeafyGreen', 'Lectern', 'LetterText', 'Library', 'LibraryBig', 'LifeBuoy', 'Ligature',
    'Lightbulb', 'LightbulbOff', 'LineSquiggle', 'Link', 'Link2', 'Link2Off', 'Linkedin', 'List',
    'ListCheck', 'ListChecks', 'ListCollapse', 'ListEnd', 'ListFilter', 'ListFilterPlus', 'ListMinus', 'ListMusic',
    'ListOrdered', 'ListPlus', 'ListRestart', 'ListStart', 'ListTodo', 'ListTree', 'ListVideo', 'ListX',
    'Loader', 'LoaderCircle', 'LoaderPinwheel', 'Locate', 'LocateFixed', 'LocateOff', 'LocationEdit', 'Lock',
    'LockKeyhole', 'LockKeyholeOpen', 'LockOpen', 'LogIn', 'LogOut', 'Logs', 'Lollipop', 'Luggage',
    'Magnet', 'Mail', 'MailCheck', 'MailMinus', 'MailOpen', 'MailPlus', 'MailQuestionMark', 'MailSearch',
    'MailWarning', 'MailX', 'Mailbox', 'Mails', 'Map', 'MapPin', 'MapPinCheck', 'MapPinCheckInside',
    'MapPinHouse', 'MapPinMinus', 'MapPinMinusInside', 'MapPinOff', 'MapPinPlus', 'MapPinPlusInside', 'MapPinX', 'MapPinXInside',
    'MapPinned', 'MapPlus', 'Mars', 'MarsStroke', 'Martini', 'Maximize', 'Maximize2', 'Medal',
    'Megaphone', 'MegaphoneOff', 'Meh', 'MemoryStick', 'Menu', 'Merge', 'MessageCircle', 'MessageCircleCode',
    'MessageCircleDashed', 'MessageCircleHeart', 'MessageCircleMore', 'MessageCircleOff', 'MessageCirclePlus', 'MessageCircleQuestionMark', 'MessageCircleReply', 'MessageCircleWarning',
    'MessageCircleX', 'MessageSquare', 'MessageSquareCode', 'MessageSquareDashed', 'MessageSquareDiff', 'MessageSquareDot', 'MessageSquareHeart', 'MessageSquareLock',
    'MessageSquareMore', 'MessageSquareOff', 'MessageSquarePlus', 'MessageSquareQuote', 'MessageSquareReply', 'MessageSquareShare', 'MessageSquareText', 'MessageSquareWarning',
    'MessageSquareX', 'MessagesSquare', 'Mic', 'MicOff', 'MicVocal', 'Microchip', 'Microscope', 'Microwave',
    'Milestone', 'Milk', 'MilkOff', 'Minimize', 'Minimize2', 'Minus', 'Monitor', 'MonitorCheck',
    'MonitorCog', 'MonitorDot', 'MonitorDown', 'MonitorOff', 'MonitorPause', 'MonitorPlay', 'MonitorSmartphone', 'MonitorSpeaker',
    'MonitorStop', 'MonitorUp', 'MonitorX', 'Moon', 'MoonStar', 'Mountain', 'MountainSnow', 'Mouse',
    'MouseOff', 'MousePointer', 'MousePointer2', 'MousePointerBan', 'MousePointerClick', 'Move', 'Move3d', 'MoveDiagonal',
    'MoveDiagonal2', 'MoveDown', 'MoveDownLeft', 'MoveDownRight', 'MoveHorizontal', 'MoveLeft', 'MoveRight', 'MoveUp',
    'MoveUpLeft', 'MoveUpRight', 'MoveVertical', 'Music', 'Music2', 'Music3', 'Music4', 'Navigation',
    'Navigation2', 'Navigation2Off', 'NavigationOff', 'Network', 'Newspaper', 'Nfc', 'NonBinary', 'Notebook',
    'NotebookPen', 'NotebookTabs', 'NotebookText', 'NotepadText', 'NotepadTextDashed', 'Nut', 'NutOff', 'Octagon',
    'OctagonAlert', 'OctagonMinus', 'OctagonPause', 'OctagonX', 'Omega', 'Option', 'Orbit', 'Origami',
    'Package', 'Package2', 'PackageCheck', 'PackageMinus', 'PackageOpen', 'PackagePlus', 'PackageSearch', 'PackageX',
    'PaintBucket', 'PaintRoller', 'Paintbrush', 'PaintbrushVertical', 'Palette', 'Panda', 'PanelBottom', 'PanelBottomClose',
    'PanelBottomDashed', 'PanelBottomOpen', 'PanelLeft', 'PanelLeftClose', 'PanelLeftDashed', 'PanelLeftOpen', 'PanelRight', 'PanelRightClose',
    'PanelRightDashed', 'PanelRightOpen', 'PanelTop', 'PanelTopClose', 'PanelTopDashed', 'PanelTopOpen', 'PanelsLeftBottom', 'PanelsRightBottom',
    'PanelsTopLeft', 'Paperclip', 'Parentheses', 'ParkingMeter', 'PartyPopper', 'Pause', 'PawPrint', 'PcCase',
    'Pen', 'PenLine', 'PenOff', 'PenTool', 'Pencil', 'PencilLine', 'PencilOff', 'PencilRuler',
    'Pentagon', 'Percent', 'PersonStanding', 'PhilippinePeso', 'Phone', 'PhoneCall', 'PhoneForwarded', 'PhoneIncoming',
    'PhoneMissed', 'PhoneOff', 'PhoneOutgoing', 'Pi', 'Piano', 'Pickaxe', 'PictureInPicture', 'PictureInPicture2',
    'PiggyBank', 'Pilcrow', 'PilcrowLeft', 'PilcrowRight', 'Pill', 'PillBottle', 'Pin', 'PinOff',
    'Pipette', 'Pizza', 'Plane', 'PlaneLanding', 'PlaneTakeoff', 'Play', 'Plug', 'Plug2',
    'PlugZap', 'Plus', 'Pocket', 'PocketKnife', 'Podcast', 'Pointer', 'PointerOff', 'Popcorn',
    'Popsicle', 'PoundSterling', 'Power', 'PowerOff', 'Presentation', 'Printer', 'PrinterCheck', 'Projector',
    'Proportions', 'Puzzle', 'Pyramid', 'QrCode', 'Quote', 'Rabbit', 'Radar', 'Radiation',
    'Radical', 'Radio', 'RadioReceiver', 'RadioTower', 'Radius', 'RailSymbol', 'Rainbow', 'Rat',
    'Ratio', 'Receipt', 'ReceiptCent', 'ReceiptEuro', 'ReceiptIndianRupee', 'ReceiptJapaneseYen', 'ReceiptPoundSterling', 'ReceiptRussianRuble',
    'ReceiptSwissFranc', 'ReceiptText', 'RectangleCircle', 'RectangleEllipsis', 'RectangleGoggles', 'RectangleHorizontal', 'RectangleVertical', 'Recycle',
    'Redo', 'Redo2', 'RedoDot', 'RefreshCcw', 'RefreshCcwDot', 'RefreshCw', 'RefreshCwOff', 'Refrigerator',
    'Regex', 'RemoveFormatting', 'Repeat', 'Repeat1', 'Repeat2', 'Replace', 'ReplaceAll', 'Reply',
    'ReplyAll', 'Rewind', 'Ribbon', 'Rocket', 'RockingChair', 'RollerCoaster', 'Rotate3d', 'RotateCcw',
    'RotateCcwKey', 'RotateCcwSquare', 'RotateCw', 'RotateCwSquare', 'Route', 'RouteOff', 'Router', 'Rows2',
    'Rows3', 'Rows4', 'Rss', 'Ruler', 'RulerDimensionLine', 'RussianRuble', 'Sailboat', 'Salad',
    'Sandwich', 'Satellite', 'SatelliteDish', 'SaudiRiyal', 'Save', 'SaveAll', 'SaveOff', 'Scale',
    'Scale3d', 'Scaling', 'Scan', 'ScanBarcode', 'ScanEye', 'ScanFace', 'ScanHeart', 'ScanLine',
    'ScanQrCode', 'ScanSearch', 'ScanText', 'School', 'Scissors', 'ScissorsLineDashed', 'ScreenShare', 'ScreenShareOff',
    'Scroll', 'ScrollText', 'Search', 'SearchCheck', 'SearchCode', 'SearchSlash', 'SearchX', 'Section',
    'Send', 'SendHorizontal', 'SendToBack', 'SeparatorHorizontal', 'SeparatorVertical', 'Server', 'ServerCog', 'ServerCrash',
    'ServerOff', 'Settings', 'Settings2', 'Shapes', 'Share', 'Share2', 'Sheet', 'Shell',
    'Shield', 'ShieldAlert', 'ShieldBan', 'ShieldCheck', 'ShieldEllipsis', 'ShieldHalf', 'ShieldMinus', 'ShieldOff',
    'ShieldPlus', 'ShieldQuestionMark', 'ShieldUser', 'ShieldX', 'Ship', 'ShipWheel', 'Shirt', 'ShoppingBag',
    'ShoppingBasket', 'ShoppingCart', 'Shovel', 'ShowerHead', 'Shredder', 'Shrimp', 'Shrink', 'Shrub',
    'Shuffle', 'Sigma', 'Signal', 'SignalHigh', 'SignalLow', 'SignalMedium', 'SignalZero', 'Signature',
    'Signpost', 'SignpostBig', 'Siren', 'SkipBack', 'SkipForward', 'Skull', 'Slack', 'Slash',
    'Slice', 'SlidersHorizontal', 'SlidersVertical', 'Smartphone', 'SmartphoneCharging', 'SmartphoneNfc', 'Smile', 'SmilePlus',
    'Snail', 'Snowflake', 'SoapDispenserDroplet', 'Sofa', 'Soup', 'Space', 'Spade', 'Sparkle',
    'Sparkles', 'Speaker', 'Speech', 'SpellCheck', 'SpellCheck2', 'Spline', 'SplinePointer', 'Split',
    'Spool', 'SprayCan', 'Sprout', 'Square', 'SquareActivity', 'SquareArrowDown', 'SquareArrowDownLeft', 'SquareArrowDownRight',
    'SquareArrowLeft', 'SquareArrowOutDownLeft', 'SquareArrowOutDownRight', 'SquareArrowOutUpLeft', 'SquareArrowOutUpRight', 'SquareArrowRight', 'SquareArrowUp', 'SquareArrowUpLeft',
    'SquareArrowUpRight', 'SquareAsterisk', 'SquareBottomDashedScissors', 'SquareChartGantt', 'SquareCheck', 'SquareCheckBig', 'SquareChevronDown', 'SquareChevronLeft',
    'SquareChevronRight', 'SquareChevronUp', 'SquareCode', 'SquareDashed', 'SquareDashedBottom', 'SquareDashedBottomCode', 'SquareDashedKanban', 'SquareDashedMousePointer',
    'SquareDashedTopSolid', 'SquareDivide', 'SquareDot', 'SquareEqual', 'SquareFunction', 'SquareKanban', 'SquareLibrary', 'SquareM',
    'SquareMenu', 'SquareMinus', 'SquareMousePointer', 'SquareParking', 'SquareParkingOff', 'SquarePen', 'SquarePercent', 'SquarePi',
    'SquarePilcrow', 'SquarePlay', 'SquarePlus', 'SquarePower', 'SquareRadical', 'SquareRoundCorner', 'SquareScissors', 'SquareSigma',
    'SquareSlash', 'SquareSplitHorizontal', 'SquareSplitVertical', 'SquareSquare', 'SquareStack', 'SquareTerminal', 'SquareUser', 'SquareUserRound',
    'SquareX', 'SquaresExclude', 'SquaresIntersect', 'SquaresSubtract', 'SquaresUnite', 'Squircle', 'SquircleDashed', 'Squirrel',
    'Stamp', 'Star', 'StarHalf', 'StarOff', 'StepBack', 'StepForward', 'Stethoscope', 'Sticker',
    'StickyNote', 'Store', 'StretchHorizontal', 'StretchVertical', 'Strikethrough', 'Subscript', 'Sun', 'SunDim',
    'SunMedium', 'SunMoon', 'SunSnow', 'Sunrise', 'Sunset', 'Superscript', 'SwatchBook', 'SwissFranc',
    'SwitchCamera', 'Sword', 'Swords', 'Syringe', 'Table', 'Table2', 'TableCellsMerge', 'TableCellsSplit',
    'TableColumnsSplit', 'TableOfContents', 'TableProperties', 'TableRowsSplit', 'Tablet', 'TabletSmartphone', 'Tablets', 'Tag',
    'Tags', 'Tally1', 'Tally2', 'Tally3', 'Tally4', 'Tally5', 'Tangent', 'Target',
    'Telescope', 'Tent', 'TentTree', 'Terminal', 'TestTube', 'TestTubeDiagonal', 'TestTubes', 'Text',
    'TextCursor', 'TextCursorInput', 'TextQuote', 'TextSearch', 'TextSelect', 'Theater', 'Thermometer', 'ThermometerSnowflake',
    'ThermometerSun', 'ThumbsDown', 'ThumbsUp', 'Ticket', 'TicketCheck', 'TicketMinus', 'TicketPercent', 'TicketPlus',
    'TicketSlash', 'TicketX', 'Tickets', 'TicketsPlane', 'Timer', 'TimerOff', 'TimerReset', 'ToggleLeft',
    'ToggleRight', 'Toilet', 'ToolCase', 'Tornado', 'Torus', 'Touchpad', 'TouchpadOff', 'TowerControl',
    'ToyBrick', 'Tractor', 'TrafficCone', 'TrainFront', 'TrainFrontTunnel', 'TrainTrack', 'TramFront', 'Transgender',
    'Trash', 'Trash2', 'TreeDeciduous', 'TreePalm', 'TreePine', 'Trees', 'Trello', 'TrendingDown',
    'TrendingUp', 'TrendingUpDown', 'Triangle', 'TriangleAlert', 'TriangleDashed', 'TriangleRight', 'Trophy', 'Truck',
    'TruckElectric', 'Turtle', 'Tv', 'TvMinimal', 'TvMinimalPlay', 'Twitch', 'Twitter', 'Type',
    'TypeOutline', 'Umbrella', 'UmbrellaOff', 'Underline', 'Undo', 'Undo2', 'UndoDot', 'UnfoldHorizontal',
    'UnfoldVertical', 'Ungroup', 'University', 'Unlink', 'Unlink2', 'Unplug', 'Upload', 'Usb',
    'User', 'UserCheck', 'UserCog', 'UserLock', 'UserMinus', 'UserPen', 'UserPlus', 'UserRound',
    'UserRoundCheck', 'UserRoundCog', 'UserRoundMinus', 'UserRoundPen', 'UserRoundPlus', 'UserRoundSearch', 'UserRoundX', 'UserSearch',
    'UserX', 'Users', 'UsersRound', 'Utensils', 'UtensilsCrossed', 'UtilityPole', 'Variable', 'Vault',
    'VectorSquare', 'Vegan', 'VenetianMask', 'Venus', 'VenusAndMars', 'Vibrate', 'VibrateOff', 'Video',
    'VideoOff', 'Videotape', 'View', 'Voicemail', 'Volleyball', 'Volume', 'Volume1', 'Volume2',
    'VolumeOff', 'VolumeX', 'Vote', 'Wallet', 'WalletCards', 'WalletMinimal', 'Wallpaper', 'Wand',
    'WandSparkles', 'Warehouse', 'WashingMachine', 'Watch', 'Waves', 'WavesLadder', 'Waypoints', 'Webcam',
    'Webhook', 'WebhookOff', 'Weight', 'Wheat', 'WheatOff', 'WholeWord', 'Wifi', 'WifiCog',
    'WifiHigh', 'WifiLow', 'WifiOff', 'WifiPen', 'WifiZero', 'Wind', 'WindArrowDown', 'Wine',
    'WineOff', 'Workflow', 'Worm', 'WrapText', 'Wrench', 'X', 'Youtube', 'Zap',
    'ZapOff', 'ZoomIn', 'ZoomOut',
)

_FALLBACK_ICON = "Zap"

#: Pre-#84 curated names missing from the pinned registry (renamed upstream
#: in lucide-react): lowercased alias -> canonical registry name. Verified
#: exhaustive against lucide-react 0.525.0: full registry diff showed zero
#: missing and zero extras, with wand2 the only old curated name gone.
_LEGACY_ICON_ALIASES = {
    "wand2": "WandSparkles",
}


def used_icons(*, exclude_label: str = "", exclude_kind: str = "") -> list[str]:
    """Icons already used by other custom actions / flow templates.

    Sorted unique non-blank icons from the store, excluding the row being
    edited (``exclude_label`` + ``exclude_kind``: ``"action"`` / ``"flow"``
    / ``""``). Never raises: store failures yield ``[]`` so suggestion
    still works on a fresh/erroring DB.
    """
    try:
        from . import store as _store
        seen: list[str] = []
        seen_lower: set[str] = set()

        def _collect(rows: list[dict] | None, kind: str) -> None:
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                if (exclude_kind == kind and
                        str(row.get("label") or "") == (exclude_label or "")):
                    continue
                icon = str(row.get("icon") or "").strip()
                if icon and icon.lower() not in seen_lower:
                    seen_lower.add(icon.lower())
                    seen.append(icon)

        _collect(_store.list_actions(), "action")
        _collect(_store.list_templates(), "flow")
        return sorted(seen, key=str.lower)
    except Exception:
        return []

def union_used_icons(explicit: list[str] | tuple[str, ...] | None, *,
                     exclude_label: str = "",
                     exclude_kind: str = "") -> list[str]:
    """Union explicit ``--used-icons`` with the store-collected fallback.

    The current-row exclusion still applies to the store half; explicit
    entries are kept verbatim. Case-insensitive dedup, sorted output.
    """
    seen_lower: set[str] = set()
    merged: list[str] = []
    for u in list(explicit or []) + used_icons(exclude_label=exclude_label,
                                               exclude_kind=exclude_kind):
        name_u = str(u or "").strip()
        if name_u and name_u.lower() not in seen_lower:
            seen_lower.add(name_u.lower())
            merged.append(name_u)
    return sorted(merged, key=str.lower)


def suggest_prompt(*, kind: str, name: str, label: str, prompt: str,
                   steps: str, pre: str, post: str,
                   used: list[str] | tuple[str, ...] | None = None) -> str:
    """Single inline prompt for the bare judge-style icon session.

    Everything the model needs travels inline (no repo context) — this is
    what keeps the call cheap. The contract: answer with exactly one icon
    name on the last line. ``used`` names icons already taken by other
    actions/templates — the model must avoid them so every button keeps a
    distinct icon.
    """
    seen_lower: set[str] = set()
    taken: list[str] = []
    for u in (used or []):
        name_u = str(u or "").strip()
        if name_u and name_u.lower() not in seen_lower:
            seen_lower.add(name_u.lower())
            taken.append(name_u)
    taken.sort(key=str.lower)
    parts = [
        "Pick the single best Lucide icon name for this workagent "
        f"{kind} (custom action / flow template button).\n",
        f"Name: {(name or '').strip()[:200]}\n",
        f"Label: {(label or '').strip()[:200]}\n",
        f"Prompt: {(prompt or '').strip()[:2000]}\n",
        f"Steps: {(steps or '').strip()[:4000]}\n",
        f"Pre-execution script: {(pre or '').strip()[:2000]}\n",
        f"Post-execution script: {(post or '').strip()[:2000]}\n",
    ]
    if taken:
        parts.append(
            "\nThese icons are already used by other actions/templates — "
            "do NOT suggest any of them (pick something visually distinct):\n"
            + ", ".join(taken) + "\n")
    parts.append(
        "\nValid choices (pick exactly one, prefer a specific match over "
        "a generic one; every name below is a real Lucide icon):\n"
        + ", ".join(SUGGEST_ICON_CHOICES)
        + "\n\nReply with exactly the icon name on the last line, e.g.:\n"
        "Rocket")
    return "".join(parts)


def parse_suggested_icon(text: str) -> str | None:
    """Parse the model answer down to one icon name (None when unusable).

    Accepts a bare trailing token (``Rocket``) or a trailing JSON line
    (``{"icon": "Rocket"}`` — the ``extract_append_prompt`` convention);
    matching is case-insensitive but the returned value keeps the canonical
    full-list casing.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    last = ""
    for line in text.strip().splitlines():
        line = line.strip().strip("\"'`")
        if line:
            last = line
    if not last:
        return None
    try:
        obj = json.loads(last)
        if isinstance(obj, dict):
            cand = str(obj.get("icon", "") or "").strip()
            if cand:
                last = cand
    except (json.JSONDecodeError, ValueError):
        pass
    token = re.sub(r"[^A-Za-z0-9_-]", "", last.split()[-1] if last.split() else "")
    if not token:
        return None
    lowered = {c.lower(): c for c in SUGGEST_ICON_CHOICES}
    hit = lowered.get(token.lower())
    if hit is not None:
        return hit
    # Legacy aliases from the pre-#84 curated list that are no longer in the
    # pinned lucide-react registry (renamed upstream): map to their
    # canonical successor so stored/typed values keep working.
    return _LEGACY_ICON_ALIASES.get(token.lower())


def validate_suggested_icon(icon: str | None) -> str:
    """Validated icon name; raises HarnessError (exit 2) when unusable."""
    from .errors import HarnessError
    if not icon:
        raise HarnessError(
            "the AI session returned no usable icon — try again or pick one manually",
            exit_code=2)
    return icon


def run_suggest_icon(prompt_text: str, *, harness: str | None = None,
                     timeout_s: float = 120) -> str:
    """Run ONE bare judge-style harness session and return its last text.

    Cheap by construction: ``judge_argv`` (cheap model, no skills/tools/
    rules/context/LSP/extensions/pty) in a fresh temp dir — the prompt
    carries everything inline, no repo checkout needed. Raises
    HarnessError when the harness is missing, times out, exits non-zero,
    or mints no transcript.
    """
    from . import backend, store
    from . import store_sqlite as _sq
    from .errors import HarnessError
    harness_name = harness or store.default_harness()
    try:
        harness_obj = backend.get_harness(harness_name)
    except HarnessError:
        raise HarnessError(
            f"unsupported harness: {harness_name!r}", exit_code=2) from None
    with tempfile.TemporaryDirectory(prefix="workagent-suggest-icon-") as directory:
        judge_path = str(Path(directory) / "judge.jsonl")
        flags = harness_obj.session_file_flag(judge_path)
        try:
            argv_extra = [*harness_obj.judge_argv, *flags]
            code = backend.launch(
                harness_name, prompt_text, directory, True, argv_extra,
                env_file=None, watch=None, timeout_s=timeout_s,
                session_file=judge_path)
        except HarnessError:
            raise
        except Exception as e:
            raise HarnessError(f"icon suggestion failed: {e}", exit_code=1) from e
        target = (Path(judge_path)
                  if (Path(judge_path).is_file() and Path(judge_path).stat().st_size > 0)
                  else (_sq.newest_transcript(Path(directory)) if Path(directory).is_dir() else None))
        if code != 0 or target is None:
            raise HarnessError(
                f"icon suggestion failed (exit {code}) — "
                "the AI session produced no answer", exit_code=1)
        text = harness_obj.extract_last_assistant_text(str(target), max_chars=None)
        if not (text or "").strip():
            raise HarnessError(
                "icon suggestion produced an empty answer — try again",
                exit_code=1)
        return text
