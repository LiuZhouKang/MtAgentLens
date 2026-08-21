# Makefile for performance benchmark target programs
# These are used by Exp 8 (Performance Micro-benchmarks)

CC = gcc
CFLAGS = -O2 -Wall
LDFLAGS_SSL = -lssl -lcrypto
LDFLAGS_FILE =

TARGETS = ssl_loop exec_loop file_loop

.PHONY: all clean

all: $(TARGETS)

ssl_loop: ssl_loop.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS_SSL)

exec_loop: exec_loop.c
	$(CC) $(CFLAGS) -o $@ $<

file_loop: file_loop.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS_FILE)

clean:
	rm -f $(TARGETS)
