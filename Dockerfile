FROM ubuntu:20.04

RUN apt-get update && apt-get install -y gcc make libc-dev

COPY mtlinpack.c /usr/src/mtlinpack.c

RUN gcc -DSP -O4 /usr/src/mtlinpack.c -lm -DROLL -o /usr/src/mtlinpack.ex2 -w

ENTRYPOINT ["/usr/src/mtlinpack.ex2"]