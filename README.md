# Order service
This project implements a series of RESTful APIs to manage the creation, deletion, modification and retrival of an order. It also implements and API that can get a list of optionally filtered orders.
Endpoints documentation: once the service is running, you can go to "http://localhost:8000/docs" to view the complete documentation of the APIs implemented and you can test each one of them directly via the interface. Also you can see the Openapi at "http://localhost:8000/openapi.json"

# Considerations / possible evolutions
Add an endpoint to view product stock
Add a field "status" to orders, only the orders not in status "delivered" can be cancelled, otherwise the operation is blocked
