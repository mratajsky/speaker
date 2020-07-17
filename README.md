# Network audio speaker

This package contains software for a network audio speaker and 
the `speaker-cli` script for managing speakers in the local network.

The following features are available:
  * **Integrated audio player**: each speaker can be instructed to play audio from a given URL
  * **Integrated spotify client**: each speaker can run a [spotifyd](https://github.com/Spotifyd/spotifyd) client
  * **Audio streaming**: hardware audio inputs of each speaker are automatically available on the local network as RTP streams, which other speakers can connect to
  * **RPC and zeroconf networking**: speakers broadcast their presence on the local network and can be controlled using [gRPC](https://grpc.io/)

Some features would be great to have in the future:
  * Bluetooth: receive audio input with bluetooth and be able to send audio into bluetooth devices
  * Greater flexibility: allow choosing audio output(s) on a device and fine-tune its inputs
  * Phone client

## Dependencies and installation
  * Python 3.7 or greater
  * Working PulseAudio installation
  * Redis server
  * GStreamer library with the *base* and *good* plugin sets and the RTSP server library (libgstrtspserver)
  * Python GIR bindings for GStreamer (Gst and GstRtspServer)

The program, along with its Python dependencies, can be installed with `pip3 install .` in the main directory.

## How to use

Install the program on a Linux board equipped with a set of speakers and run the `speaker` executable.

The `speaker-cli` tool can then be used to control the speaker(s) from a computer in the local network.

Examples:
  * `speaker-cli list`: list the available speakers
  * `speaker-cli play http://your-favorite-radio-station speaker1`: play the URL on speaker1
  * `speaker-cli spotify-creds -a user password`: set Spotify credentials at all available speakers
  * `speaker-cli spotify-start speaker1`: run Spotify on speaker1
  * `speaker-cli -h`: show all available commands
