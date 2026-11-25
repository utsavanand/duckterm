-- Only the caller's disposable disk image is changed; never Applications.
on run argv
    set imagePath to item 1 of argv
    set appName to item 2 of argv
    tell application "Finder"
        set imageFolder to (POSIX file imagePath) as alias
        open imageFolder
        set imageWindow to container window of imageFolder
        set current view of imageWindow to icon view
        set toolbar visible of imageWindow to false
        set statusbar visible of imageWindow to false
        set bounds of imageWindow to {200, 150, 840, 554}
        set viewOptions to icon view options of imageWindow
        set arrangement of viewOptions to not arranged
        set icon size of viewOptions to 104
        set text size of viewOptions to 14
        set background picture of viewOptions to file ".background:installer.png" of imageFolder
        set position of item appName of imageFolder to {190, 165}
        set position of item "Applications" of imageFolder to {450, 165}
        update imageFolder without registering applications
        delay 2
        close imageWindow
    end tell
end run
