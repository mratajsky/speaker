#!/usr/bin/env python3

import argparse
from speaker.main import run


def parse_args():
    parser = argparse.ArgumentParser(description='Network speaker device.')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='enable debugging messages')
    parser.add_argument('--host', type=str,
                        help='IP address to bind network services to')
    parser.add_argument('--grpc-port', type=int,
                        help='port for the gRPC service')
    parser.add_argument('--rtsp-port', type=int,
                        help='port for the RTSP service')
    parser.add_argument('--spotifyd-path', type=str,
                        help='path to a spotifyd binary')
    return parser.parse_args()


if __name__ == '__main__':
    run(parse_args())
