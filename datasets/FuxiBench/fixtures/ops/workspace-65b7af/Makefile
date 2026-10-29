SERVICES := api-gateway auth-service billing-service inventory-service notification-service

.PHONY: all test clean

all:
	@for s in $(SERVICES); do $(MAKE) -C services/$$s clean build test || exit 1; done

test:
	@for s in $(SERVICES); do $(MAKE) -C services/$$s clean test || exit 1; done

clean:
	@for s in $(SERVICES); do $(MAKE) -C services/$$s clean; done
