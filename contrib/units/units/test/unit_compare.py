

import Scientific.Physics.PhysicalQuantities as case1
import units as case2
import time

n = 20000

#x1 = case1.PhysicalQuantity('5cm')
#x2 = case2.PhysicalQuantity('5cm')


t1 = time.time()

for jj in xrange(n):
    pq = case1.PhysicalQuantity(5, 'mi/h')
    pq.convertToUnit('m/s')
    
print "Scientific -> Elapsed time: ", time.time()-t1
print ""

t2 = time.time()

for jj in xrange(n):
    pq = case2.PhysicalQuantity(5, 'mi/h')
    pq.convertToUnit('m/s')
    
print "Justin -> Elapsed time: ", time.time()-t2
