# SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
# Production builds must not link AddressSanitizer: the collector embeds these
# helper binaries, and LeakSanitizer turns a normal SIGTERM shutdown into
# exit_status 1 (capture quality "degraded"). `make debug` opts in explicitly.
.DEFAULT_GOAL := all

OUTPUT := .output
BIN_DIR := $(OUTPUT)/bin
CLANG ?= clang
LIBBPF_SRC := $(abspath ../libbpf/src)
BPFTOOL_SRC := $(abspath ../bpftool/src)
LIBBPF_OBJ := $(abspath $(OUTPUT)/libbpf.a)
BPFTOOL_OUTPUT ?= $(abspath $(OUTPUT)/bpftool)
BPFTOOL ?= $(BPFTOOL_OUTPUT)/bootstrap/bpftool
LIBBLAZESYM_SRC := $(abspath ..//blazesym/)
LIBBLAZESYM_OBJ := $(abspath $(OUTPUT)/libblazesym.a)
LIBBLAZESYM_HEADER := $(abspath $(OUTPUT)/blazesym.h)
ARCH ?= $(shell uname -m | sed 's/x86_64/x86/' \
			 | sed 's/arm.*/arm/' \
			 | sed 's/aarch64/arm64/' \
			 | sed 's/ppc64le/powerpc/' \
			 | sed 's/mips.*/mips/' \
			 | sed 's/riscv64/riscv/' \
			 | sed 's/loongarch64/loongarch/')
VMLINUX := ../vmlinux/$(ARCH)/vmlinux.h
# Use our own libbpf API headers and Linux UAPI headers distributed with
# libbpf to avoid dependency on system-wide headers, which could be missing or
# outdated
# Include paths: output dir, root, shared headers, each program dir, libbpf, vmlinux
# BPF program directories — each contains <name>.bpf.c, <name>.c, <name>.h
BPF_PROGS = tls_capture stdiocap process

# Test programs (no BPF skeleton needed)
TEST_APPS = test_process_snapshot test_process_capture test_process_utils test_process_filter test_process_map_flush test_process_mem_info test_process_header test_tls_capture_boringssl_offsets test_tls_capture_header test_json_escape test_b64 test_payload_json test_process_file_ops test_process_network

# All applications
APPS = $(BPF_PROGS) $(TEST_APPS)

# Include paths: output dir, root, shared headers, each program dir, libbpf, vmlinux
INCLUDES := -I$(OUTPUT) -I. -Iinclude $(addprefix -I,$(BPF_PROGS)) -I../libbpf/include/uapi -I$(dir $(VMLINUX))
CFLAGS := -g -Wall
ALL_LDFLAGS := $(LDFLAGS) $(EXTRA_LDFLAGS)

# AddressSanitizer flags
ifdef ASAN
ASAN_FLAGS := -fsanitize=address -fno-omit-frame-pointer -O1 -g
CFLAGS += $(ASAN_FLAGS)
ALL_LDFLAGS += $(ASAN_FLAGS)
endif

CARGO ?= $(shell which cargo)
ifeq ($(strip $(CARGO)),)
BZS_APPS :=
else
BZS_APPS := # profile
APPS += $(BZS_APPS)
# Required by libblazesym
ALL_LDFLAGS += -lrt -ldl -lpthread -lm
endif

# Get Clang's default includes on this system. We'll explicitly add these dirs
# to the includes list when compiling with `-target bpf` because otherwise some
# architecture-specific dirs will be "missing" on some architectures/distros -
# headers such as asm/types.h, asm/byteorder.h, asm/socket.h, asm/sockios.h,
# sys/cdefs.h etc. might be missing.
#
# Use '-idirafter': Don't interfere with include mechanics except where the
# build would have failed anyways.
CLANG_BPF_SYS_INCLUDES ?= $(shell $(CLANG) -v -E - </dev/null 2>&1 \
	| sed -n '/<...> search starts here:/,/End of search list./{ s| \(/.*\)|-idirafter \1|p }')

ifeq ($(V),1)
	Q =
	msg =
else
	Q = @
	msg = @printf '  %-8s %s%s\n'					\
		      "$(1)"						\
		      "$(patsubst $(abspath $(OUTPUT))/%,%,$(2))"	\
		      "$(if $(3), $(3))";
	MAKEFLAGS += --no-print-directory
endif

define allow-override
  $(if $(or $(findstring environment,$(origin $(1))),\
            $(findstring command line,$(origin $(1)))),,\
    $(eval $(1) = $(2)))
endef

$(call allow-override,CC,$(CROSS_COMPILE)cc)
$(call allow-override,LD,$(CROSS_COMPILE)ld)

.PHONY: all debug
debug:
	$(MAKE) ASAN=1 all

.PHONY: tls_capture-debug
tls_capture-debug:
	$(MAKE) ASAN=1 tls_capture

.PHONY: clean
clean:
	$(call msg,CLEAN)
	$(Q)rm -rf $(OUTPUT)
	$(Q)for app in $(APPS); do \
		if [ -f "$$app" ]; then rm -f "$$app"; fi; \
	done

.PHONY: help
help:
	@echo "Available targets:"
	@echo "  all          - Build all applications"
	@echo "  tls_capture     - Build tls_capture only"
	@echo "  stdiocap     - Build stdiocap only"
	@echo "  process      - Build process tracer only"
	@echo "  test         - Build and run tests"
	@echo "  debug        - Build all applications with AddressSanitizer"
	@echo "  tls_capture-debug - Build tls_capture with AddressSanitizer"
	@echo "  clean        - Clean build artifacts"
	@echo ""
	@echo "Build with AddressSanitizer manually:"
	@echo "  make ASAN=1 tls_capture"

.PHONY: test
# Runs EVERY entry in TEST_APPS.
#
# This used to list five binaries by hand, which silently omitted
# test_tls_capture_header and test_tls_capture_boringssl_offsets — they were
# only reachable through their own phony targets, so a plain `make test`
# (and anything driving it) never executed them. Deriving from TEST_APPS keeps
# a newly added test from being forgotten again.
test: $(addprefix $(BIN_DIR)/,$(TEST_APPS))
	@for t in $(TEST_APPS); do \
		echo "── $$t ──"; \
		$(BIN_DIR)/$$t || exit 1; \
		echo ""; \
	done

.PHONY: test-tls_capture-boringssl-offsets
test-tls_capture-boringssl-offsets: $(BIN_DIR)/test_tls_capture_boringssl_offsets
	@echo "Running tls_capture BoringSSL offset tests..."
	@$(BIN_DIR)/test_tls_capture_boringssl_offsets

.PHONY: test-json-escape
test-json-escape: $(BIN_DIR)/test_json_escape
	@echo "Running json_escape contract tests..."
	@$(BIN_DIR)/test_json_escape

.PHONY: test-tls_capture-header
test-tls_capture-header: $(BIN_DIR)/test_tls_capture_header
	@echo "Running tls_capture header contract tests..."
	@$(BIN_DIR)/test_tls_capture_header

.PHONY: integration-test
integration-test: $(BIN_DIR)/process
	@echo "Running integration tests (requires sudo)..."
	@sudo python3 tests/test_integration.py

$(OUTPUT) $(OUTPUT)/libbpf $(BPFTOOL_OUTPUT):
	$(call msg,MKDIR,$@)
	$(Q)mkdir -p $@

# Build libbpf
$(LIBBPF_OBJ): $(wildcard $(LIBBPF_SRC)/*.[ch] $(LIBBPF_SRC)/Makefile) | $(OUTPUT)/libbpf
	$(call msg,LIB,$@)
	$(Q)$(MAKE) -C $(LIBBPF_SRC) BUILD_STATIC_ONLY=1		      \
		    OBJDIR=$(dir $@)/libbpf DESTDIR=$(dir $@)		      \
		    INCLUDEDIR= LIBDIR= UAPIDIR=			      \
		    install

# Build bpftool
$(BPFTOOL): | $(BPFTOOL_OUTPUT)
	$(call msg,BPFTOOL,$@)
	$(Q)$(MAKE) ARCH= CROSS_COMPILE= OUTPUT=$(BPFTOOL_OUTPUT)/ -C $(BPFTOOL_SRC) bootstrap


$(LIBBLAZESYM_SRC)/target/release/libblazesym.a::
	$(Q)cd $(LIBBLAZESYM_SRC) && $(CARGO) build --features=cheader,dont-generate-test-files --release

$(LIBBLAZESYM_OBJ): $(LIBBLAZESYM_SRC)/target/release/libblazesym.a | $(OUTPUT)
	$(call msg,LIB, $@)
	$(Q)cp $(LIBBLAZESYM_SRC)/target/release/libblazesym.a $@

$(LIBBLAZESYM_HEADER): $(LIBBLAZESYM_SRC)/target/release/libblazesym.a | $(OUTPUT)
	$(call msg,LIB,$@)
	$(Q)cp $(LIBBLAZESYM_SRC)/target/release/blazesym.h $@

# ── BPF build rules (one per program, sources in <prog>/<prog>.bpf.c) ──
define BPF_BUILD_RULE
$(OUTPUT)/$(1).bpf.o: $(1)/$(1).bpf.c $(LIBBPF_OBJ) $(wildcard $(1)/$(1).h include/*.h) $(VMLINUX) | $(OUTPUT) $(BPFTOOL)
	$$(call msg,BPF,$$@)
	$$(Q)$$(CLANG) -g -O2 -target bpf -D__TARGET_ARCH_$$(ARCH) \
		$$(INCLUDES) $$(CLANG_BPF_SYS_INCLUDES) \
		-c $$< -o $$(patsubst %.bpf.o,%.tmp.bpf.o,$$@)
	$$(Q)$$(BPFTOOL) gen object $$@ $$(patsubst %.bpf.o,%.tmp.bpf.o,$$@)
endef
$(foreach prog,$(BPF_PROGS),$(eval $(call BPF_BUILD_RULE,$(prog))))

# ── Skeleton generation ──
$(OUTPUT)/%.skel.h: $(OUTPUT)/%.bpf.o | $(OUTPUT) $(BPFTOOL)
	$(call msg,GEN-SKEL,$@)
	$(Q)$(BPFTOOL) gen skeleton $< > $@

# ── User-space loader build rules ──
define LOADER_BUILD_RULE
$(OUTPUT)/$(1).o: $(1)/$(1).c $(wildcard $(1)/$(1).h include/*.h) $(OUTPUT)/$(1).skel.h | $(OUTPUT)
	$$(call msg,CC,$$@)
	$$(Q)$$(CC) $$(CFLAGS) $$(INCLUDES) -c $$< -o $$@
endef
$(foreach prog,$(BPF_PROGS),$(eval $(call LOADER_BUILD_RULE,$(prog))))

# ── Test program build rules (no BPF skeleton needed) ──
$(OUTPUT)/test_process_utils.o: tests/test_process_utils.c $(wildcard include/*.h) | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OUTPUT)/test_process_filter.o: tests/test_process_filter.c $(wildcard include/*.h) | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OUTPUT)/test_process_map_flush.o: tests/test_process_map_flush.c process_ext/map_flush.h $(wildcard include/*.h) | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -Itests -c $< -o $@

$(OUTPUT)/test_process_mem_info.o: tests/test_process_mem_info.c process_ext/mem_info.h $(wildcard include/*.h) | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OUTPUT)/test_process_header.o: tests/test_process_header.c $(wildcard include/*.h) | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OUTPUT)/test_process_file_ops.o: tests/test_process_file_ops.c $(wildcard include/*.h) $(wildcard process/*.h) | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

# Pure struct-layout contract test; no BPF skeleton needed.
$(OUTPUT)/test_tls_capture_header.o: tests/test_tls_capture_header.c $(wildcard tls_capture/*.h) | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OUTPUT)/test_tls_capture_boringssl_offsets.o: tests/test_tls_capture_boringssl_offsets.c $(wildcard include/*.h) $(OUTPUT)/tls_capture.skel.h | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -Itests -ffunction-sections -fdata-sections -c $< -o $@

$(patsubst %,$(OUTPUT)/%.o,$(BZS_APPS)): $(LIBBLAZESYM_HEADER)

$(BZS_APPS): $(LIBBLAZESYM_OBJ)

# ── Binary output directory (avoids name collision with source dirs) ──

# Build application binary (exclude all test programs)
$(addprefix $(BIN_DIR)/,$(filter-out $(TEST_APPS),$(APPS))): $(BIN_DIR)/%: $(OUTPUT)/%.o $(LIBBPF_OBJ) | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -lelf -lz -o $@

# Special rules for test programs (no libbpf needed)
$(BIN_DIR)/test_process_utils: $(OUTPUT)/test_process_utils.o | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -o $@

$(BIN_DIR)/test_process_filter: $(OUTPUT)/test_process_filter.o | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -o $@

$(BIN_DIR)/test_process_file_ops: $(OUTPUT)/test_process_file_ops.o | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -o $@

# map_flush helper uses libbpf_num_possible_cpus() from libbpf.
$(BIN_DIR)/test_process_map_flush: $(OUTPUT)/test_process_map_flush.o $(LIBBPF_OBJ) | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -lelf -lz -o $@

$(BIN_DIR)/test_process_mem_info: $(OUTPUT)/test_process_mem_info.o | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -o $@

$(BIN_DIR)/test_process_header: $(OUTPUT)/test_process_header.o | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -o $@

$(BIN_DIR)/test_tls_capture_header: $(OUTPUT)/test_tls_capture_header.o | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -o $@

# Pure header contract test; needs no BPF skeleton.
$(OUTPUT)/test_json_escape.o: tests/test_json_escape.c $(wildcard include/*.h) | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(BIN_DIR)/test_json_escape: $(OUTPUT)/test_json_escape.o | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -o $@

# Streaming base64 contract test; needs no BPF skeleton.
$(OUTPUT)/test_b64.o: tests/test_b64.c $(wildcard include/*.h) | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(BIN_DIR)/test_b64: $(OUTPUT)/test_b64.o | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -o $@

# Payload field contract test; needs no BPF skeleton.
$(OUTPUT)/test_payload_json.o: tests/test_payload_json.c $(wildcard include/*.h) | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(BIN_DIR)/test_payload_json: $(OUTPUT)/test_payload_json.o | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -o $@

# Network event wire-format contract test; needs no BPF skeleton.
$(OUTPUT)/test_process_network.o: tests/test_process_network.c process/process.h process/process_json.h $(wildcard include/*.h) | $(OUTPUT)
	$(call msg,CC,$@)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(BIN_DIR)/test_process_network: $(OUTPUT)/test_process_network.o | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -o $@

$(BIN_DIR)/test_tls_capture_boringssl_offsets: $(OUTPUT)/test_tls_capture_boringssl_offsets.o $(LIBBPF_OBJ) | $(BIN_DIR)
	$(call msg,BINARY,$@)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -Wl,--gc-sections -lelf -lz -o $@

$(BIN_DIR):
	$(Q)mkdir -p $@

# Default target depends on all binaries
# Production binaries only. Test binaries are built by their own targets
# (`make test` depends on them) — building them into .output/bin on every
# `make` was pure waste.
all: $(addprefix $(BIN_DIR)/,$(BPF_PROGS))

# delete failed targets
.DELETE_ON_ERROR:

# keep intermediate (.skel.h, .bpf.o, etc) targets
.SECONDARY:

$(OUTPUT)/test_process_capture.o: tests/test_process_capture.c process/process.c $(wildcard process/*.h include/*.h) $(OUTPUT)/process.skel.h | $(OUTPUT)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -ffunction-sections -fdata-sections -c $< -o $@

$(BIN_DIR)/test_process_capture: $(OUTPUT)/test_process_capture.o $(LIBBPF_OBJ) | $(BIN_DIR)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -Wl,--gc-sections -lelf -lz -o $@

$(OUTPUT)/test_process_snapshot.o: tests/test_process_snapshot.c process/process_snapshot.h process/process.h include/process_filter.h | $(OUTPUT)
	$(Q)$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(BIN_DIR)/test_process_snapshot: $(OUTPUT)/test_process_snapshot.o | $(BIN_DIR)
	$(Q)$(CC) $(CFLAGS) $^ $(ALL_LDFLAGS) -o $@
