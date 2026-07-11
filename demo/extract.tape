# extract.tape — real comfybulk extract against committed fixture clips
# House conventions per auto_demo/demo/render-hero.sh + cassette.tape.

Output extract-raw.gif

Set FontSize 18
Set Width 1200
Set Height 720
Set Padding 20
Set Theme "Catppuccin Frappe"
Set WaitTimeout 60s

Type "comfybulk --config config.example.toml extract --path tests/fixtures/samples --csv out.csv"
Sleep 800ms
Enter
Wait+Screen /Extracted 3, failed 0/
Sleep 2s

Type "cut -d, -f2,3 out.csv | column -s, -t"
Sleep 800ms
Enter
Wait+Screen /determinism/
Sleep 3.5s
