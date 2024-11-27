[Setup]
AppName=Bot Musforums
AppVersion=1.0
DefaultDirName={pf}\Bot Musforums
DefaultGroupName=Bot Musforums
OutputDir=Output
OutputBaseFilename=BotMusforums_Setup
PrivilegesRequired=admin
;SetupIconFile=myicon.ico
Compression=lzma2
SolidCompression=yes

[Files]
; Копируем все файлы из папки dist
Source: "dist\Bot_Musforums\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Bot Musforums"; Filename: "{app}\Bot_Musforums.exe"
Name: "{commondesktop}\Bot Musforums"; Filename: "{app}\Bot_Musforums.exe"

[Run]
; Опция запуска программы после установки
Filename: "{app}\Bot_Musforums.exe"; Description: "Запустить Bot Musforums"; Flags: postinstall nowait skipifsilent 