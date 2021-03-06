import numpy
import mpsort
from mpi4py import MPI

class EmptyRankType(object):
    def __repr__(self):
        return "EmptyRank"
EmptyRank = EmptyRankType()

class LinearTopology(object):
    """ Helper object for the topology of a distributed array 
    """ 
    def __init__(self, local, comm):
        self.local = local
        self.comm = comm

    def heads(self):
        """
        The first items on each rank. 
        
        Returns
        -------
        heads : list
            a list of first items, EmptyRank is used for empty ranks
        """

        head = EmptyRank
        if len(self.local) > 0:
            head = self.local[0]

        return self.comm.allgather(head)

    def tails(self):
        """
        The last items on each rank. 
        
        Returns
        -------
        tails: list
            a list of last items, EmptyRank is used for empty ranks
        """
        tail = EmptyRank
        if len(self.local) > 0:
            tail = self.local[-1]

        return self.comm.allgather(tail)

    def prev(self):
        """
        The item before the local data.

        This method fetches the last item before the local data.
        If the rank before is empty, the rank before is used. 

        If no item is before this rank, EmptyRank is returned

        Returns
        -------
        prev : scalar
            Item before local data, or EmptyRank if all ranks before this rank is empty.

        """

        tails = [EmptyRank]
        oldtail = EmptyRank
        for tail in self.tails():
            if tail is EmptyRank:
                tails.append(oldtail)
            else:
                tails.append(tail)
                oldtail = tail
        prev = tails[self.comm.rank]
        return prev

    def next(self):
        """
        The item after the local data.

        This method the first item after the local data. 
        If the rank after current rank is empty, 
        item after that rank is used. 

        If no item is after local data, EmptyRank is returned.

        Returns
        -------
        next : scalar
            Item after local data, or EmptyRank if all ranks after this rank is empty.

        """
        heads = []
        oldhead = EmptyRank
        for head in self.heads():
            if head is EmptyRank:
                heads.append(oldhead)
            else:
                heads.append(head)
                oldhead = head
        heads.append(EmptyRank)

        next = heads[self.comm.rank + 1]
        return next
    

class DistributedArray(object):
    """
    Distributed Array Object

    A distributed array is striped along ranks

    Attributes
    ----------
    comm : :py:class:`mpi4py.MPI.Comm`
        the communicator

    local : array_like
        the local data

    """
    def __init__(self, local, comm=MPI.COMM_WORLD):
        self.local = local
        self.comm = comm
        self.topology = LinearTopology(local, comm)

    def sort(self, orderby=None):
        """
        Sort array globally by key orderby.

        Due to a limitation of mpsort, self[orderby] must be u8.

        """
        mpsort.sort(self.local, orderby)

    def __getitem__(self, key):
        return DistributedArray(self.local[key], self.comm)

    def unique_labels(self):
        """
        Assign unique labels to sorted local. 

        .. warning ::

            local data must be sorted, and of simple type. (numpy.unique)

        Returns
        -------
        label   :  :py:class:`DistributedArray`
            the new labels, starting from 0

        """
        prev, next = self.topology.prev(), self.topology.next()
         
        junk, label = numpy.unique(self.local, return_inverse=True)
        if len(self.local) == 0:
            Nunique = 0
        else:
            # watch out: this is to make sure after shifting first 
            # labels on the next rank is the same as my last label
            # when there is a spill-over.
            if next == self.local[-1]:
                Nunique = len(junk) - 1
            else:
                Nunique = len(junk)

        label += sum(self.comm.allgather(Nunique)[:self.comm.rank])
        return DistributedArray(label, self.comm)

    def bincount(self, local=False):
        """
        Assign count numbers from sorted local data.

        .. warning ::

            local data must be sorted, and of integer type. (numpy.bincount)

        Parameters
        ----------
        local : boolean
            if local is True, only count the local array.

        Returns
        -------
        N :  :py:class:`DistributedArray`
            distributed counts array. If items of the same value spans other
            chunks of array, they are added to N as well.

        Examples
        --------
        if the local array is [ (0, 0), (0, 1)], 
        Then the counts array is [ (3, ), (3, 1)]
        """
        prev = self.topology.prev()
        if prev is not EmptyRank:
            offset = prev
            if len(self.local) > 0:
                if prev != self.local[0]:
                    offset = self.local[0]
        else:
            offset = 0

        N = numpy.bincount(self.local - offset)

        if local:
            return N

        heads = self.topology.heads()
        tails = self.topology.tails()

        distN = DistributedArray(N, self.comm)
        headsN, tailsN = distN.topology.heads(), distN.topology.tails()

        if len(N) > 0:
            for i in reversed(range(self.comm.rank)):
                if tails[i] == self.local[0]:
                    N[0] += tailsN[i]
            for i in range(self.comm.rank + 1, self.comm.size):
                if heads[i] == self.local[-1]:
                    N[-1] += headsN[i]

        return DistributedArray(N, self.comm)

def test():
    comm = MPI.COMM_WORLD
    local = numpy.empty((comm.rank), 
            dtype=[('key', 'u8'), ('value', 'u8'), ('rank', 'i8')])
    d = DistributedArray(local)
    local['key'] = numpy.arange(len(local))
    local['value'] = d.comm.rank * 10 + local['key']
    local['rank'] = d.comm.rank

    print d.topology.heads()

    a = d.comm.allgather(d.local['key'])
    if d.comm.rank == 0:
        print 'old', a

    d.sort('key')
    a = d.comm.allgather(d.local['key'])
    if d.comm.rank == 0:
        print 'new', a

    u = d['key'].unique_labels()
    a = d.comm.allgather(u.local)
    if d.comm.rank == 0:
        print 'unique', a

    N = u.bincount()
    a = d.comm.allgather(N.local)
    if d.comm.rank == 0:
        print 'count', a

    N = u.bincount(local=True)
    a = d.comm.allgather(N)
    if d.comm.rank == 0:
        print 'count local', a

    d['key'].local[:] = u.local
    d.sort('value')

    a = d.comm.allgather(d.local['value'])
    if d.comm.rank == 0:
        print 'back', a

if __name__ == '__main__': 
    test()
