#!/bin/bash

echo "Creating VENV..."
rm -R -f venv
python3 -m venv venv

echo "Installing requirements..."
source venv/bin/activate
python3 -m pip install -r requirements.txt

echo "Creating Splunk shell script..."
echo "#!/bin/bash" > ../api_poller.sh
GIT_PATH=`pwd`
cd ..
SPLUNK_SCRIPTS_PATH=`pwd`
echo "source $GIT_PATH/venv/bin/activate" > ../api_poller.sh
echo "python3 $GIT_PATH/scripts/api_poller.py $SPLUNK_SCRIPTS_PATH/settings.json" >> ../api_poller.sh
chmod +x ../api_poller.sh

echo "================================"
echo "Copy the following to your inputs.conf file"
echo "================================"
echo "## START COPY ##"
echo ""
echo "[script://$SPLUNK_SCRIPTS_PATH/api_poller.sh]"
echo "disabled = 0"
echo "interval = 1"
echo "index = api"
echo ""
echo "## END COPY ##"
echo "================================"
echo "Place your 'settings.json' file here: $SPLUNK_SCRIPTS_PATH"
echo ""