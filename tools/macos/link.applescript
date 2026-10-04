-- Investment Monitor's monitor:// link (install-link.sh builds it into ~/Applications). Only the two exact
-- links act, each running a fixed word; the link's text is never handed to the shell.
property linkScript : "__SCRIPT__"

on open location theURL
	if theURL is "monitor://open" or theURL is "monitor://open/" then
		runLink("open")
	else if theURL is "monitor://stop" or theURL is "monitor://stop/" then
		runLink("stop")
	end if
end open location

on run
	runLink("open")
end run

on runLink(action)
	try
		do shell script quoted form of linkScript & " " & action
	end try
end runLink
