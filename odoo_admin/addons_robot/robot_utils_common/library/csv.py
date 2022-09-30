import csv as Csv


class csv(object):

    def read_csv_file(self, filename):
        '''
        This keyword takes one argument, which is a path to a .csv file. It
        returns a list of the first data row.
        We always consider the row 0 with labels.
        '''
        data = []
        with open(filename, 'rb') as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                data.append(row)
        if len(data) <= 1:
            return []
        return data[1:][0]

    def read_csv_dict(self, filename):
        '''
        This keyword takes one argument, which is a path to a .csv file. It
        returns a list of the first data row.
        We always consider the row 0 with labels.
        '''
        data = []
        with open(filename, 'rb') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                data.append(row)
        return data
